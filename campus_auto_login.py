#!/usr/bin/env python3
import argparse
import base64
import ctypes
import getpass
import http.client
import json
import os
import queue
import random
import re
import socket
import sys
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError, URLError


DEFAULT_PORTAL = "http://10.200.84.3"
APP_NAME = "YAU-AutoNet-Connect"
APP_VERSION = "1.0.4"
__version__ = APP_VERSION

# Legacy urllib opener kept for backward compatibility; v1.0.4 core path uses http.client direct.
DIRECT_OPENER = request.build_opener(request.ProxyHandler({}))


def open_direct(req, timeout=10):
    """Legacy fallback: open a request using DIRECT_OPENER to bypass system proxy."""
    return DIRECT_OPENER.open(req, timeout=timeout)


def fetch_direct_text(url, headers=None, timeout=10):
    """Fetch URL content using raw http.client.HTTPConnection (bypasses urllib proxy entirely)."""
    parsed = parse.urlsplit(url)
    if parsed.scheme and parsed.scheme != "http":
        raise ValueError("fetch_direct_text only supports http:// URLs, got: {0}".format(parsed.scheme))
    host = parsed.hostname
    port = parsed.port or 80
    path_with_query = parsed.path or "/"
    if parsed.query:
        path_with_query = "{0}?{1}".format(path_with_query, parsed.query)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path_with_query, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
        if status >= 400:
            raise OSError("HTTP {0} {1} from {2}".format(status, resp.reason, url))
        return body.decode("utf-8", errors="replace")
    finally:
        conn.close()


def fetch_direct_raw(url, headers=None, timeout=10):
    """Like fetch_direct_text but returns (status, reason, headers_dict, body_text).
    Used for portal discovery where we need to inspect redirects."""
    parsed = parse.urlsplit(url)
    if parsed.scheme and parsed.scheme != "http":
        raise ValueError("fetch_direct_raw only supports http:// URLs")
    host = parsed.hostname
    port = parsed.port or 80
    path_with_query = parsed.path or "/"
    if parsed.query:
        path_with_query = "{0}?{1}".format(path_with_query, parsed.query)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", path_with_query, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, resp.reason, dict(resp.getheaders()), body
    finally:
        conn.close()


def _is_socket_unreachable_error(exc):
    """Check if an exception is a socket-level reachability error (transient)."""
    msg = str(exc)
    return (
        "[WinError 10065]" in msg
        or "Network is unreachable" in msg
        or "timed out" in msg.lower()
        or "[Errno 110]" in msg  # ETIMEDOUT on Linux
        or "[Errno 101]" in msg  # ENETUNREACH
        or "[Errno 113]" in msg  # EHOSTUNREACH
    )


def fetch_text_with_retry(url, headers=None, timeout=10, retries=None):
    """fetch_direct_text with short retry on socket-level errors.
    Returns (text, attempt_number) where attempt is 1-based."""
    if retries is None:
        retries = [1, 3, 5]  # retry delays in seconds
    last_exc = None
    for attempt, delay in enumerate([0] + retries):
        if delay > 0:
            time.sleep(delay)
        try:
            text = fetch_direct_text(url, headers=headers, timeout=timeout)
            return text, attempt + 1
        except (OSError, ValueError) as exc:
            if not _is_socket_unreachable_error(exc) or attempt >= len(retries):
                raise
            last_exc = exc
    raise last_exc


# ---------------------------------------------------------------------------
# Resilient portal transport: 4-layer fallback stack
# ---------------------------------------------------------------------------

_VIRTUAL_KEYWORDS_NET = [
    "vmware", "virtualbox", "hyper-v", "virtual", "secitap", "sectap",
    "netease", "uu", "tun", "tap", "wintun", "wireguard", "vpn",
    "clash", "meta", "mihomo", "sstap", "loopback", "teredo",
    "isatap", "6to4",
]

_preferred_source_ip = [None]  # cached after first successful interface-bound connection


def _get_physical_adapter_ips():
    """Get IPv4 addresses from physical network adapters, excluding virtual ones.
    Returns list of (ip_address, ifIndex, alias, description) tuples."""
    results = []
    try:
        out = os.popen(
            'powershell.exe -NoProfile -Command "'
            'Get-NetAdapter | Where-Object {$_.Status -eq \'Up\'} | ForEach-Object { '
            '$ip = Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | '
            'Where-Object {$_.IPAddress -ne \'127.0.0.1\' -and $_.IPAddress -notlike \'169.254.*\'} | Select-Object -First 1; '
            'if ($ip) { \'{0}|{1}|{2}|{3}\' -f $ip.IPAddress, $_.ifIndex, $_.Name, $_.InterfaceDescription }'
            '}"'
        ).read(4096).strip()
        for line in out.splitlines():
            if "|" not in line:
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            ip, ifidx, name, desc = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            combined = (name + " " + desc).lower()
            is_virtual = any(kw in combined for kw in _VIRTUAL_KEYWORDS_NET)
            results.append((ip, ifidx, name, desc, is_virtual))
    except Exception:
        pass
    return results


def _find_preferred_source_ip(portal_host="10.200.84.3", portal_port=80, timeout=3):
    """Find the best local IP to bind for reaching the portal.
    Tests each physical adapter's IP with source_address binding.
    Returns (ip, ifidx, alias) or None."""
    if _preferred_source_ip[0]:
        return _preferred_source_ip[0]

    adapters = _get_physical_adapter_ips()
    # Separate physical and virtual
    physical = [(ip, ifidx, name, desc) for ip, ifidx, name, desc, virt in adapters if not virt]
    virtual = [(ip, ifidx, name, desc) for ip, ifidx, name, desc, virt in adapters if virt]

    # Try physical first, then virtual as fallback
    for candidates in [physical, virtual]:
        for ip, ifidx, name, desc in candidates:
            try:
                s = socket.create_connection((portal_host, portal_port), timeout=timeout, source_address=(ip, 0))
                s.close()
                _preferred_source_ip[0] = (ip, ifidx, name)
                return _preferred_source_ip[0]
            except OSError:
                continue
    return None


def fetch_direct_with_source(url, source_ip, headers=None, timeout=10):
    """Like fetch_direct_text but binds to a specific local IP."""
    parsed = parse.urlsplit(url)
    host = parsed.hostname
    port = parsed.port or 80
    path_with_query = parsed.path or "/"
    if parsed.query:
        path_with_query = "{0}?{1}".format(path_with_query, parsed.query)
    conn = http.client.HTTPConnection(host, port, timeout=timeout, source_address=(source_ip, 0))
    try:
        conn.request("GET", path_with_query, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise OSError("HTTP {0} {1} from {2}".format(resp.status, resp.reason, url))
        return body.decode("utf-8", errors="replace")
    finally:
        conn.close()


def _powershell_no_proxy_fetch(url, timeout=10):
    """Fetch URL using PowerShell's .NET WebClient with proxy explicitly bypassed."""
    try:
        # Use .NET WebClient with UseDefaultCredentials=false and no proxy
        ps_cmd = (
            '[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12; '
            '$wc = New-Object System.Net.WebClient; '
            '$wc.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy; '
            '$wc.Headers.Add(\"User-Agent\", \"campus-auto-login\"); '
            '$wc.DownloadString(\"{0}\")'
        ).format(url.replace('"', '`"'))
        result = os.popen(
            'powershell.exe -NoProfile -Command "{0}"'.format(ps_cmd)
        ).read(8192).strip()
        if result and len(result) > 5:
            return result
    except Exception:
        pass
    return None


def _get_system_proxy_settings():
    """Read current Windows system proxy settings for safe restore."""
    settings = {"ProxyEnable": 0, "ProxyServer": "", "ProxyOverride": ""}
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_READ,
        ) as key:
            try:
                settings["ProxyEnable"], _ = winreg.QueryValueEx(key, "ProxyEnable")
            except (OSError, ValueError):
                pass
            try:
                settings["ProxyServer"], _ = winreg.QueryValueEx(key, "ProxyServer")
            except (OSError, ValueError):
                pass
            try:
                settings["ProxyOverride"], _ = winreg.QueryValueEx(key, "ProxyOverride")
            except (OSError, ValueError):
                pass
    except (OSError, ValueError):
        pass
    return settings


def _set_system_proxy(enable, server="", override=""):
    """Set Windows system proxy and notify the system of the change."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
            if server:
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
            if override:
                winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, override)
        # Notify system of proxy change
        try:
            import ctypes as _ct
            INTERNET_OPTION_SETTINGS_CHANGED = 39
            INTERNET_OPTION_REFRESH = 37
            _ct.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
            _ct.windll.wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _temporary_proxy_bypass_fetch(url, headers=None, timeout=10):
    """Temporarily disable system proxy, fetch, then restore. Returns text or None."""
    original = _get_system_proxy_settings()
    try:
        # Temporarily disable system proxy
        _set_system_proxy(False)
        time.sleep(0.3)  # brief pause for system to apply
        # Try direct fetch
        text = fetch_direct_text(url, headers=headers, timeout=timeout)
        return text
    except Exception:
        return None
    finally:
        # Always restore original proxy settings
        _set_system_proxy(
            enable=original["ProxyEnable"],
            server=original["ProxyServer"],
            override=original["ProxyOverride"],
        )


def fetch_portal_text_resilient(url, headers=None, timeout=10, purpose="status",
                                 allow_proxy_bypass=False):
    """Resilient 4-layer fetch for campus portal URLs.
    Layer 1: raw http.client direct
    Layer 2: interface-bound raw direct (bind to physical adapter IP)
    Layer 3: PowerShell .NET WebClient with no proxy
    Layer 4: temporary Windows proxy bypass (if allowed)

    Returns (text, layer_name) on success, raises OSError if all layers fail.
    """
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-python", "Accept": "*/*"}

    errors = []

    # Layer 1: raw http.client direct
    try:
        text = fetch_direct_text(url, headers=headers, timeout=timeout)
        return text, "raw_direct"
    except (OSError, ValueError) as e:
        errors.append("raw_direct: {0}".format(e))

    # Layer 2: interface-bound raw direct
    parsed = parse.urlsplit(url)
    portal_host = parsed.hostname or "10.200.84.3"
    portal_port = parsed.port or 80
    src = _find_preferred_source_ip(portal_host, portal_port, timeout=min(timeout, 5))
    if src:
        try:
            text = fetch_direct_with_source(url, src[0], headers=headers, timeout=timeout)
            return text, "interface_bound({0})".format(src[0])
        except (OSError, ValueError) as e:
            errors.append("interface_bound({0}): {1}".format(src[0], e))
    else:
        errors.append("interface_bound: no local interface can reach portal")

    # Layer 3: PowerShell .NET WebClient no-proxy
    ps_result = _powershell_no_proxy_fetch(url, timeout=min(timeout, 15))
    if ps_result:
        return ps_result, "powershell_no_proxy"
    errors.append("powershell_no_proxy: failed")

    # Layer 4: temporary proxy bypass (only if allowed)
    if allow_proxy_bypass:
        text = _temporary_proxy_bypass_fetch(url, headers=headers, timeout=timeout)
        if text:
            return text, "temp_proxy_bypass"
        errors.append("temp_proxy_bypass: failed")
    else:
        errors.append("temp_proxy_bypass: not enabled (use --allow-temporary-proxy-bypass)")

    raise OSError("All transport layers failed for {0}: {1}".format(url, "; ".join(errors)))


def ensure_process_proxy_bypass_for_portal(portal_hosts=None):
    """Add portal hosts to NO_PROXY env var for this process only.
    Does NOT modify Windows system proxy or registry."""
    if portal_hosts is None:
        portal_hosts = ["10.200.84.3", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    extra = ",".join(portal_hosts + ["localhost", "127.0.0.1"])
    for var in ("NO_PROXY", "no_proxy"):
        existing = os.environ.get(var, "")
        if existing:
            os.environ[var] = existing + "," + extra
        else:
            os.environ[var] = extra


def app_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


SCRIPT_DIR = app_base_dir()


def _reattach_console():
    """windowed 模式下双击无终端，但从 cmd 运行时需要恢复输出到父终端。"""
    if not getattr(sys, "frozen", False):
        return
    if ctypes.windll.kernel32.GetConsoleWindow():
        return
    if ctypes.windll.kernel32.AttachConsole(-1):
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8")


_reattach_console()
DEFAULT_CONFIG = SCRIPT_DIR / "campus_login_py.config.json"
DEFAULT_PS_CONFIG = SCRIPT_DIR / "campus_login.config.json"
DEFAULT_LOG = SCRIPT_DIR / "campus_auto_login_py.log"
MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 30

_log_queue = queue.Queue()
_log_lock = threading.Lock()


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _require_windows():
    if sys.platform != "win32":
        raise RuntimeError("DPAPI password storage only works on Windows.")


def dpapi_protect(text):
    _require_windows()
    data = text.encode("utf-8")
    in_buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect_bytes(data):
    _require_windows()
    in_buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(encoded):
    return dpapi_unprotect_bytes(base64.b64decode(encoded)).decode("utf-8")


def dpapi_unprotect_powershell_secure_string(hex_text):
    return dpapi_unprotect_bytes(bytes.fromhex(hex_text)).decode("utf-16le")


def write_log(log_path, message):
    line = "[{0}] {1}".format(time.strftime("%Y-%m-%d %H:%M:%S"), message)
    with _log_lock:
        print(line)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # 日志轮转：超过 1MB 时归档为 .old
        try:
            if log_path.exists() and log_path.stat().st_size > 1_048_576:
                old = log_path.with_suffix(".log.old")
                old.unlink(missing_ok=True)
                log_path.rename(old)
        except OSError:
            pass
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    try:
        _log_queue.put_nowait(line)
    except queue.Full:
        pass


def jsonp_to_obj(text):
    text = text.strip()
    match = re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*\((.*)\)\s*;?\s*$", text, re.S)
    if match:
        return json.loads(match.group(1))
    if text.startswith("{"):
        return json.loads(text)
    raise ValueError("Response is not JSONP/JSON:{0}".format(text[:120]))


def query_string(pairs):
    return parse.urlencode([(str(k), str(v)) for k, v in pairs], doseq=False)


def invoke_jsonp(portal_base, path, params=None, timeout=10, force_trailing_lang=False,
                  allow_proxy_bypass=False):
    params = list(params or [])
    keys = {k for k, _ in params}
    callback = "dr{0}".format(random.randint(100000, 999999))
    all_params = [("callback", callback)]
    all_params.extend(params)
    if "jsVersion" not in keys:
        all_params.append(("jsVersion", "4.X"))
    all_params.append(("v", random.randint(500, 10500)))
    if force_trailing_lang or "lang" not in keys:
        all_params.append(("lang", "zh"))
    url = "{0}{1}?{2}".format(portal_base.rstrip("/"), path, query_string(all_params))
    headers = {
        "User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-python",
        "Accept": "*/*",
    }
    content, _layer = fetch_portal_text_resilient(
        url, headers=headers, timeout=timeout,
        purpose="jsonp", allow_proxy_bypass=allow_proxy_bypass,
    )
    return jsonp_to_obj(content)


def invoke_url_jsonp(url_base, params=None, timeout=10, force_trailing_lang=False,
                      portal_base=None, allow_proxy_bypass=False):
    params = list(params or [])
    keys = {k for k, _ in params}
    callback = "dr{0}".format(random.randint(100000, 999999))
    all_params = [("callback", callback)]
    all_params.extend(params)
    if "jsVersion" not in keys:
        all_params.append(("jsVersion", "4.2.1"))
    all_params.append(("v", random.randint(500, 10500)))
    if force_trailing_lang or "lang" not in keys:
        all_params.append(("lang", "zh"))
    url = "{0}?{1}".format(url_base, query_string(all_params))
    referer = (portal_base or DEFAULT_PORTAL).rstrip("/") + "/"
    headers = {
        "User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-python",
        "Accept": "*/*",
        "Referer": referer,
    }
    content, _layer = fetch_portal_text_resilient(
        url, headers=headers, timeout=timeout,
        purpose="login", allow_proxy_bypass=allow_proxy_bypass,
    )
    return jsonp_to_obj(content)


def eportal_login_url(portal_base):
    parsed = parse.urlparse(portal_base.rstrip("/"))
    host = parsed.hostname or "10.200.84.3"
    scheme = parsed.scheme or "http"
    return "{0}://{1}:801/eportal/portal/login".format(scheme, host)


def account_prefix(terminal_type):
    return ",1," if int(terminal_type) == 2 else ",0,"


def client_info_from_status(status_raw):
    status_raw = status_raw or {}
    ip = (
        status_raw.get("v4ip")
        or status_raw.get("v46ip")
        or status_raw.get("ss5")
        or status_raw.get("lip")
        or ""
    )
    mac = status_raw.get("ss4") or status_raw.get("olmac") or "000000000000"
    if not mac:
        mac = "000000000000"
    return str(ip), str(mac).replace("-", "").replace(":", "")


def read_config(config_path):
    if not config_path.exists():
        if config_path == DEFAULT_CONFIG and DEFAULT_PS_CONFIG.exists():
            return read_powershell_config(DEFAULT_PS_CONFIG)
        raise FileNotFoundError("Config not found.Run:first python campus_auto_login.py --init")
    with config_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not data.get("username") or not data.get("password_dpapi"):
        raise ValueError("Config misses username or password_dpapi.Run --init again.")
    data["config_format"] = "python"
    return data


def read_powershell_config(config_path):
    with config_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not data.get("Username") or not data.get("Password"):
        raise ValueError("PowerShell config misses Username or Password.")
    return {
        "portal_base": str(data.get("PortalBase") or DEFAULT_PORTAL).rstrip("/"),
        "username": str(data["Username"]),
        "password_ps_hex": str(data["Password"]),
        "service_suffix": str(data.get("ServiceSuffix") or ""),
        "terminal_type": int(data.get("TerminalType") or 1),
        "config_format": "powershell",
    }


def init_config(args):
    username = input("Campus username:")
    password = getpass.getpass("Campus password:")
    suffix = input("Service suffix(empty for default,@dx for telecom,@lt for unicom):")
    data = {
        "portal_base": args.portal_base.rstrip("/"),
        "username": username,
        "password_dpapi": dpapi_protect(password),
        "service_suffix": suffix,
        "terminal_type": args.terminal_type,
    }
    args.config.parent.mkdir(parents=True, exist_ok=True)
    with args.config.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    write_log(args.log, "Config created:{0}".format(args.config))


def get_status(portal_base, allow_proxy_bypass=False):
    """Check portal status using resilient transport.
    Returns dict with 'state': online/offline/network_not_ready/portal_unreachable.
    """
    url = "{0}/drcom/chkstatus?callback=_ck&jsVersion=4.X&v={1}&lang=zh".format(
        portal_base.rstrip("/"), random.randint(500, 10500)
    )
    headers = {
        "User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-python",
        "Accept": "*/*",
    }
    try:
        content, layer = fetch_portal_text_resilient(
            url, headers=headers, timeout=8,
            purpose="status", allow_proxy_bypass=allow_proxy_bypass,
        )
        data = jsonp_to_obj(content)
        result_val = int(data.get("result", 0))
        return {
            "state": "online" if result_val == 1 else "offline",
            "reachable": True,
            "online": result_val == 1,
            "raw": data,
            "error": None,
            "layer": layer,
        }
    except (OSError, ValueError) as exc:
        is_socket = _is_socket_unreachable_error(exc)
        return {
            "state": "network_not_ready" if is_socket else "portal_unreachable",
            "reachable": False,
            "online": False,
            "raw": None,
            "error": str(exc),
            "is_network_unreachable": is_socket,
            "layer": None,
        }


def login_once(config, args, failure_state=None):
    """Attempt one login cycle. Returns True if online after this call.
    failure_state: dict to track consecutive failures across calls (for tray loop).
    """
    allow_bypass = getattr(args, "allow_temporary_proxy_bypass", False)
    status = get_status(config["portal_base"], allow_proxy_bypass=allow_bypass)

    if status["state"] == "network_not_ready":
        # Socket-level failure - transient, likely Wi-Fi roaming or network transition
        if failure_state is not None:
            failure_state["consecutive_failures"] = failure_state.get("consecutive_failures", 0) + 1
            count = failure_state["consecutive_failures"]
        else:
            count = 1

        if count <= 1:
            write_log(args.log, "Portal temporarily unreachable, will retry next cycle.")
        elif count == 2:
            write_log(args.log, "Portal still unreachable ({0} consecutive). Running auto-discovery...".format(count))
            discovered = discover_portal_base(
                config["portal_base"], timeout=3,
                log_fn=lambda msg: write_log(args.log, msg),
            )
            if discovered.rstrip("/") != config["portal_base"].rstrip("/"):
                write_log(args.log, "Switching to discovered portal: {0}".format(discovered))
                config["portal_base"] = discovered
        elif count >= 3:
            write_log(args.log, "Portal unreachable ({0} consecutive). Route exists but TCP failed. Waiting for network.".format(count))
            maybe_diagnose(config["portal_base"], args.log)
        return False

    if status["state"] == "portal_unreachable":
        # HTTP-level error, not transient socket
        write_log(args.log, "Portal HTTP error: {0}".format(status["error"]))
        if failure_state is not None:
            failure_state["consecutive_failures"] = failure_state.get("consecutive_failures", 0) + 1
        # Try portal auto-discovery
        discovered = discover_portal_base(
            config["portal_base"], timeout=3,
            log_fn=lambda msg: write_log(args.log, msg),
        )
        if discovered.rstrip("/") != config["portal_base"].rstrip("/"):
            write_log(args.log, "Switching to discovered portal: {0}".format(discovered))
            config["portal_base"] = discovered
            status = get_status(config["portal_base"])
            if not status["reachable"]:
                return False
        else:
            return False

    # Portal is reachable - reset failure counter
    if failure_state is not None:
        failure_state["consecutive_failures"] = 0

    if status.get("attempts", 1) > 1:
        write_log(args.log, "Portal reached after {0} attempts.".format(status["attempts"]))
    if status["online"]:
        write_log(args.log, "Already online.No login needed.")
        return True
    write_log(args.log, "Offline from portal status.Login will be attempted.")
    if args.check:
        return False

    if config.get("password_dpapi"):
        password = dpapi_unprotect(config["password_dpapi"])
    elif config.get("password_ps_hex"):
        password = dpapi_unprotect_powershell_secure_string(config["password_ps_hex"])
    else:
        raise ValueError("Config has no supported password field.")
    raw_account = "{0}{1}".format(config["username"], config.get("service_suffix", ""))
    terminal_type = int(config.get("terminal_type", 1))
    account = account_prefix(terminal_type) + raw_account
    wlan_user_ip, wlan_user_mac = client_info_from_status(status["raw"])
    params = [
        ("login_method", "1"),
        ("user_account", account),
        ("user_password", password),
        ("wlan_user_ip", wlan_user_ip),
        ("wlan_user_ipv6", ""),
        ("wlan_user_mac", wlan_user_mac),
        ("wlan_ac_ip", ""),
        ("wlan_ac_name", ""),
        ("jsVersion", "4.2.1"),
        ("terminal_type", terminal_type),
        ("lang", "zh-cn"),
    ]
    try:
        for index in range(1, args.max_attempts + 1):
            result = invoke_url_jsonp(
                eportal_login_url(config["portal_base"]),
                params,
                timeout=12,
                force_trailing_lang=True,
                portal_base=config["portal_base"],
                allow_proxy_bypass=allow_bypass,
            )
            result_value = result.get("result")
            if result_value == 1 or str(result_value).lower() in {"1", "ok"}:
                write_log(args.log, "Login API returned success.")
                time.sleep(2)
                after = get_status(config["portal_base"], allow_proxy_bypass=allow_bypass)
                if after["online"]:
                    write_log(args.log, "Status recheck confirmed online.")
                    return True
                write_log(args.log, "Status recheck still offline after login.")
            else:
                message = (
                    result.get("msg")
                    or result.get("error_msg")
                    or result.get("ErrorMsg")
                    or result.get("ret_code")
                    or result.get("result")
                    or "unknown error"
                )
                write_log(args.log, "Login failed:{0}".format(message))
            if index < args.max_attempts:
                time.sleep(args.retry_seconds)
        return False
    finally:
        password = None


def check_only(args):
    status = get_status(args.portal_base)
    if not status["reachable"]:
        write_log(args.log, "Portal unreachable:{0}".format(status["error"]))
        return 2
    if status["online"]:
        write_log(args.log, "Already online.")
        return 0
    write_log(args.log, "Offline from portal status.")
    return 1


def parse_args():
    parser = argparse.ArgumentParser(description="Campus network auto login for Dr.COM portal.")
    parser.add_argument("--version", action="version", version="{0} {1}".format(APP_NAME, APP_VERSION))
    parser.add_argument("--init", action="store_true", help="Create encrypted config.")
    parser.add_argument("--once", action="store_true", help="Check once and login if offline.")
    parser.add_argument("--check", action="store_true", help="Only check portal status.")
    parser.add_argument("--interval", type=int, default=30, help="Monitor interval seconds.")
    parser.add_argument("--retry-seconds", type=int, default=10, help="Delay between retries.")
    parser.add_argument("--max-attempts", type=int, default=3, help="Max login attempts per cycle.")
    parser.add_argument("--portal-base", default=DEFAULT_PORTAL, help="Portal base URL.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Config path.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="Log path.")
    parser.add_argument("--terminal-type", type=int, default=1, help="1 for PC,2 for mobile.")
    parser.add_argument("--tray", action="store_true", help="Run in system tray background mode.")
    parser.add_argument("--diagnose", action="store_true", help="Run portal connectivity diagnostic and exit.")
    parser.add_argument("--allow-temporary-proxy-bypass", action="store_true",
                        help="Allow temporarily disabling Windows system proxy for portal access.")
    parser.add_argument("--check-wifi", action="store_true", help="Check current WiFi SSID and exit.")
    parser.add_argument("--set-campus-ssid", action="store_true", help="Save current WiFi SSID as campus SSID.")
    parser.add_argument("--campus-ssid", default="", help="Campus WiFi SSID for auto-reconnect.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# WiFi SSID detection
# ---------------------------------------------------------------------------

def get_current_wifi_ssid():
    """Get the current WiFi SSID (read-only, best-effort)."""
    try:
        out = os.popen('netsh wlan show interfaces').read(4096)
        for line in out.splitlines():
            if "SSID" in line and "BSSID" not in line:
                parts = line.split(":", 1)
                if len(parts) >= 2:
                    ssid = parts[1].strip()
                    if ssid:
                        return ssid
    except Exception:
        pass
    return ""


def check_wifi_and_warn(campus_ssid, log_fn=None):
    """Check if current WiFi matches campus SSID. Returns True if OK."""
    current = get_current_wifi_ssid()
    if not current:
        if log_fn:
            log_fn("Current Wi-Fi SSID: <not connected or not Wi-Fi>")
        return True  # can't determine, don't block
    if not campus_ssid:
        if log_fn:
            log_fn("Current Wi-Fi SSID: {0} (no campus SSID configured)".format(current))
        return True
    if current.lower() == campus_ssid.lower():
        if log_fn:
            log_fn("Wi-Fi SSID matches campus: {0}".format(current))
        return True
    if log_fn:
        log_fn("WARNING: Current Wi-Fi SSID '{0}' != campus SSID '{1}'. Portal may be unreachable.".format(
            current, campus_ssid))
    return False


def normalize_interval(seconds):
    if seconds < MIN_INTERVAL_SECONDS:
        return MIN_INTERVAL_SECONDS
    if seconds > MAX_INTERVAL_SECONDS:
        return MAX_INTERVAL_SECONDS
    return seconds


# ---------------------------------------------------------------------------
# Portal auto-discovery
# ---------------------------------------------------------------------------

# Common connectivity-check URLs that campus networks often redirect to portal
_NCSI_PROBE_URLS = [
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://connectivitycheck.gstatic.com/generate_204",
    "http://neverssl.com/",
    "http://captive.apple.com/hotspot-detect.html",
]


def _extract_portal_from_url(url):
    """Extract potential portal base URL from a redirect URL."""
    parsed = parse.urlsplit(url)
    if not parsed.hostname:
        return None
    host = parsed.hostname
    # Skip if it's a known public host
    skip_hosts = {
        "www.msftconnecttest.com", "connectivitycheck.gstatic.com",
        "neverssl.com", "captive.apple.com",
    }
    if host in skip_hosts:
        return None
    scheme = parsed.scheme or "http"
    port = parsed.port
    if port and port != 80:
        return "{0}://{1}:{2}".format(scheme, host, port)
    return "{0}://{1}".format(scheme, host)


def _test_portal_candidate(base_url, timeout=3):
    """Test if a URL is a reachable campus portal by checking /drcom/chkstatus."""
    try:
        text = fetch_direct_text(
            "{0}/drcom/chkstatus?callback=_test&jsVersion=4.X&v=1&lang=zh".format(base_url.rstrip("/")),
            headers={"User-Agent": "Mozilla/5.0 campus-auto-login-discovery"},
            timeout=timeout,
        )
        # Check if response looks like JSONP with result field
        obj = jsonp_to_obj(text)
        if isinstance(obj, dict) and "result" in obj:
            return True
    except Exception:
        pass
    return False


def _get_default_gateway():
    """Get the default gateway IP (read-only, best-effort)."""
    try:
        output = os.popen("route print 0.0.0.0").read(2048)
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "0.0.0.0":
                gw = parts[2]
                if gw and gw != "0.0.0.0":
                    return gw
    except Exception:
        pass
    return None


def _get_gateway_subnet_candidates(gateway, count=5):
    """Generate candidate portal IPs from the default gateway's subnet.
    Tries the gateway itself and nearby addresses."""
    if not gateway:
        return []
    parts = gateway.split(".")
    if len(parts) != 4:
        return []
    candidates = []
    # Try the gateway itself
    candidates.append("http://{0}".format(gateway))
    # Try the .1 and .3 addresses in the same subnet
    base = ".".join(parts[:3])
    for suffix in [1, 3, 2, 100, 254]:
        ip = "{0}.{1}".format(base, suffix)
        if ip != gateway:
            candidates.append("http://{0}".format(ip))
    return candidates[:count]


def discover_portal_base(configured_portal_base, timeout=3, log_fn=None):
    """Discover the campus portal base URL.
    Priority: configured portal -> DEFAULT_PORTAL -> gateway subnet -> NCSI redirects.
    Returns the working portal base URL, or the configured one if nothing found.
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    configured = configured_portal_base.rstrip("/")

    # 1. Try configured portal
    if _test_portal_candidate(configured, timeout=timeout):
        _log("Portal confirmed: {0}".format(configured))
        return configured

    _log("Portal {0} not reachable, trying candidates...".format(configured))

    # 2. Try DEFAULT_PORTAL if different from configured
    default = DEFAULT_PORTAL.rstrip("/")
    if default != configured and _test_portal_candidate(default, timeout=timeout):
        _log("Portal confirmed via default: {0}".format(default))
        return default

    # 3. Try gateway subnet (local, no internet needed)
    gateway = _get_default_gateway()
    if gateway:
        for candidate in _get_gateway_subnet_candidates(gateway):
            if candidate.rstrip("/") in (configured, default):
                continue
            if _test_portal_candidate(candidate, timeout=timeout):
                _log("Discovered portal via gateway subnet: {0}".format(candidate))
                return candidate

    # 4. NCSI redirects (requires some network access, may fail without internet)
    for probe_url in _NCSI_PROBE_URLS:
        try:
            status, reason, headers, body = fetch_direct_raw(
                probe_url,
                headers={"User-Agent": "Mozilla/5.0 campus-auto-login-discovery"},
                timeout=2,
            )
            location = headers.get("Location") or headers.get("location", "")
            if location:
                candidate = _extract_portal_from_url(location)
                if candidate and candidate.rstrip("/") not in (configured, default):
                    if _test_portal_candidate(candidate, timeout=timeout):
                        _log("Discovered portal via redirect: {0}".format(candidate))
                        return candidate
        except Exception:
            continue

    return configured


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------

_last_diagnose_time = 0
_DIAGNOSE_COOLDOWN = 600  # 10 minutes between detailed diagnostics


def _check_system_proxy_enabled():
    """Read Windows system proxy registry (read-only, no modification)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_READ,
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            return bool(enabled)
    except (OSError, ValueError):
        return False


def _check_proxy_env():
    """Check if proxy environment variables are set."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if os.environ.get(name):
            return True
    return False


def _get_portal_route_info(portal_host):
    """Get the network route/interface used to reach the portal host.
    Returns dict with ifIndex, alias, sourceIP, nextHop, metric."""
    result = {"ifIndex": None, "alias": None, "sourceIP": None, "nextHop": None, "metric": None}
    # Use route print (always available on Windows)
    try:
        route_out = os.popen("route print {0}".format(portal_host)).read(2048)
        in_active = False
        for line in route_out.splitlines():
            if "Active Routes" in line:
                in_active = True
                continue
            if in_active:
                parts = line.split()
                if len(parts) >= 5:
                    dest, mask, gw, iface, metric = parts[0], parts[1], parts[2], parts[3], parts[4]
                    if dest == portal_host or dest == "0.0.0.0":
                        result["nextHop"] = gw
                        result["sourceIP"] = iface
                        result["metric"] = metric
                        break
                if line.strip() == "":
                    in_active = False
    except Exception:
        pass
    # Try to get interface alias via PowerShell (best-effort)
    if result["sourceIP"]:
        try:
            ps_out = os.popen(
                'powershell.exe -NoProfile -Command "'
                'Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -eq \'' + result["sourceIP"] + '\'} | '
                'Select-Object -First 1 | ForEach-Object { \'%s|%s|%s\' -f $_.InterfaceIndex, $_.InterfaceAlias, $_.IPAddress }"'
                '"' % result["sourceIP"]
            ).read(512).strip()
            if ps_out and "|" in ps_out:
                parts = ps_out.split("|")
                if len(parts) >= 2:
                    result["ifIndex"] = parts[0].strip()
                    result["alias"] = parts[1].strip()
        except Exception:
            pass
    return result


def _get_proxy_details():
    """Get proxy server and ProxyOverride from registry (read-only)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_READ,
        ) as key:
            server = ""
            override = ""
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except (OSError, ValueError):
                pass
            try:
                override, _ = winreg.QueryValueEx(key, "ProxyOverride")
            except (OSError, ValueError):
                pass
            return server, override
    except (OSError, ValueError):
        return "", ""


_VIRTUAL_KEYWORDS = [
    "clash", "meta", "mihomo", "tun", "tap", "wintun", "wireguard",
    "vpn", "virtual", "vmware", "virtualbox", "hyper-v",
    "netease", "uu", "sstap", "sectap", "secitap",
]


def _detect_virtual_adapters():
    """Detect virtual/TUN/TAP adapters via Get-NetAdapter (read-only).
    Returns list of (Name, InterfaceDescription, Status, ifIndex) tuples."""
    found = []
    try:
        output = os.popen(
            'powershell.exe -NoProfile -Command "'
            'Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, ifIndex | '
            'ForEach-Object { \'{0}|{1}|{2}|{3}\' -f $_.Name, $_.InterfaceDescription, $_.Status, $_.ifIndex }"'
        ).read(8192).strip()
        for line in output.splitlines():
            if not line or "|" not in line:
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            name, desc, status, ifidx = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            combined = (name + " " + desc).lower()
            for kw in _VIRTUAL_KEYWORDS:
                if kw in combined:
                    found.append((name, desc, status, ifidx))
                    break
    except Exception:
        pass
    return found


def diagnose_portal_connectivity(portal_base, timeout=3, log_fn=None):
    """Diagnose connectivity to the campus portal. Returns a list of diagnostic lines."""
    lines = []
    parsed = parse.urlsplit(portal_base.rstrip("/"))
    host = parsed.hostname or "10.200.84.3"
    port = parsed.port or 80
    scheme = parsed.scheme or "http"

    lines.append("--- Portal Connectivity Diagnostic ---")
    lines.append("Portal host: {0}".format(host))
    lines.append("Portal base: {0}".format(portal_base.rstrip("/")))
    lines.append("Status URL: {0}/drcom/chkstatus".format(portal_base.rstrip("/")))
    lines.append("Login URL: {0}://{1}:801/eportal/portal/login".format(scheme, host))

    # Portal route info (the CORRECT interface for reaching the portal)
    route_info = _get_portal_route_info(host)
    lines.append("Portal route interface: {0} (ifIndex={1})".format(
        route_info.get("alias") or "unknown", route_info.get("ifIndex") or "?"))
    lines.append("Portal route source IPv4: {0}".format(route_info.get("sourceIP") or "unknown"))
    lines.append("Portal route next hop: {0}".format(route_info.get("nextHop") or "unknown"))
    lines.append("Portal route metric: {0}".format(route_info.get("metric") or "unknown"))

    # Default gateway
    gw = _get_default_gateway()
    lines.append("Default gateway: {0}".format(gw or "unknown"))

    # Warn if route source looks like virtual adapter
    src_ip = route_info.get("sourceIP") or ""
    if src_ip.startswith("192.168.144.") or src_ip.startswith("192.168.56.") or src_ip.startswith("172.16."):
        lines.append("WARNING: Portal route source IPv4 may belong to a virtual/host-only adapter.")

    # Virtual adapters
    vnet = _detect_virtual_adapters()
    if vnet:
        for name, desc, status, ifidx in vnet:
            lines.append("Virtual adapter: {0} ({1}) [{2}] ifIndex={3}".format(name, desc, status, ifidx))
    else:
        lines.append("Virtual adapters: none detected")

    # Socket test port 80
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        lines.append("Raw socket {0}:{1}: SUCCESS".format(host, port))
    except OSError as exc:
        lines.append("Raw socket {0}:{1}: FAIL - {2}".format(host, port, exc))

    # Socket test port 801
    try:
        sock = socket.create_connection((host, 801), timeout=timeout)
        sock.close()
        lines.append("Raw socket {0}:801: SUCCESS".format(host))
    except OSError as exc:
        lines.append("Raw socket {0}:801: FAIL - {1}".format(host, exc))

    # Raw direct HTTP status check
    try:
        text = fetch_direct_text(
            "{0}/drcom/chkstatus?callback=_diag&jsVersion=4.X&v=1&lang=zh".format(portal_base.rstrip("/")),
            headers={"User-Agent": "Mozilla/5.0 campus-auto-login-diag"},
            timeout=timeout,
        )
        obj = jsonp_to_obj(text)
        result_val = obj.get("result", "?")
        lines.append("Raw direct HTTP status: result={0} (SUCCESS)".format(result_val))
    except Exception as exc:
        lines.append("Raw direct HTTP status: FAIL - {0}".format(exc))

    # Proxy details
    proxy_server, proxy_override = _get_proxy_details()
    lines.append("Windows proxy server: {0}".format(proxy_server or "not set"))
    if proxy_override:
        short_override = proxy_override[:120] + ("..." if len(proxy_override) > 120 else "")
        lines.append("ProxyOverride: {0}".format(short_override))

    # Proxy env
    proxy_env_vals = []
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(name, "")
        if val:
            try:
                p = parse.urlsplit(val)
                proxy_env_vals.append("{0}={1}:{2}".format(name, p.hostname, p.port))
            except Exception:
                proxy_env_vals.append("{0}=<set>".format(name))
    if proxy_env_vals:
        lines.append("Proxy env vars: {0}".format(", ".join(proxy_env_vals)))
    else:
        lines.append("Proxy env vars: none")

    # NO_PROXY check
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    has_portal_in_noproxy = "10." in no_proxy or "10.200.84.3" in no_proxy
    lines.append("NO_PROXY includes portal subnet: {0}".format("YES" if has_portal_in_noproxy else "NO"))

    lines.append("--- End Diagnostic ---")

    if log_fn:
        for line in lines:
            log_fn(line)
    return lines


def maybe_diagnose(portal_base, log_path):
    """Output diagnostic if portal unreachable, throttled to once per 10 minutes."""
    global _last_diagnose_time
    now = time.time()
    if now - _last_diagnose_time < _DIAGNOSE_COOLDOWN:
        return
    _last_diagnose_time = now
    diagnose_portal_connectivity(portal_base, log_fn=lambda msg: write_log(log_path, msg))


def wait_for_portal_ready(portal_base, timeout_seconds=60, interval=5, log_fn=None,
                           allow_proxy_bypass=False):
    """Wait for the portal to become reachable. Returns status dict when ready, or None on timeout."""
    deadline = time.time() + timeout_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        status = get_status(portal_base, allow_proxy_bypass=allow_proxy_bypass)
        if status["state"] in ("online", "offline"):
            if log_fn:
                log_fn("Portal reachable after {0}s ({1} attempts) via {2}.".format(
                    int(attempt * interval), attempt, status.get("layer", "?")))
            return status
        if log_fn and attempt == 1:
            log_fn("Waiting for portal to become reachable (timeout={0}s)...".format(timeout_seconds))
        remaining = deadline - time.time()
        sleep_time = min(interval, max(1, remaining))
        if sleep_time <= 0:
            break
        time.sleep(sleep_time)
    if log_fn:
        log_fn("Portal not reachable after {0}s timeout.".format(timeout_seconds))
    return None


# ---------------------------------------------------------------------------
# Tray mode: system tray icon + tkinter log window
# ---------------------------------------------------------------------------

_CTRL_HANDLER = None  # prevent garbage collection


def _console_ctrl_handler(ctrl_type):
    """Ignore console close events so closing the console window doesn't kill the process."""
    return True


def hide_console_window():
    """Hide the console window and prevent close from killing the process."""
    global _CTRL_HANDLER
    # 注册控制台事件处理，关闭窗口时不让进程退出
    _CTRL_HANDLER = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(_console_ctrl_handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_CTRL_HANDLER, True)
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE


def create_tray_icon_image():
    """Generate a simple 64x64 tray icon image using PIL."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 120, 212, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([8, 8, 56, 56], radius=8, fill=(255, 255, 255, 230))
    draw.text((20, 14), "N", fill=(0, 120, 212, 255))
    return img


_log_window = None  # (toplevel, text_widget)
_log_drain_id = None
_show_log_event = threading.Event()
_tk_root = None


def _drain_log_queue(widget, text_widget):
    """Periodically pull log lines from the queue into the text widget."""
    global _log_drain_id
    while True:
        try:
            line = _log_queue.get_nowait()
            text_widget.insert("end", line + "\n")
            text_widget.see("end")
        except queue.Empty:
            break
    _log_drain_id = widget.after(200, _drain_log_queue, widget, text_widget)


def _create_log_window():
    """Create the tkinter log window as a Toplevel of the hidden root."""
    global _log_window, _log_drain_id
    if _log_window is not None:
        win, text_widget = _log_window
        win.deiconify()
        win.lift()
        return

    import tkinter as tk
    from tkinter.scrolledtext import ScrolledText

    win = tk.Toplevel(_tk_root)
    win.title("Campus Auto Login - 日志")
    win.geometry("640x420")
    win.resizable(True, True)

    text_widget = ScrolledText(win, font=("Consolas", 9), wrap="word", state="normal")
    text_widget.pack(fill="both", expand=True, padx=4, pady=4)

    def on_close():
        win.withdraw()

    win.protocol("WM_DELETE_WINDOW", on_close)
    _log_window = (win, text_widget)
    _drain_log_queue(win, text_widget)


def show_log_window(icon=None, item=None):
    """Signal the main thread to create/show the log window."""
    _show_log_event.set()


def quit_app(icon, item):
    """Stop the tray icon and exit the process."""
    icon.stop()
    import os
    os._exit(0)


# ---------------------------------------------------------------------------
# Auto-start (Windows registry HKCU\...\Run)
# ---------------------------------------------------------------------------

_REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_VALUE_NAME = "CampusAutoLogin"


def _exe_path():
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve())
    return str(Path(__file__).resolve())


def is_auto_start_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _REG_VALUE_NAME)
            # val 格式: '"C:\path\exe" --tray'，需要提取 exe 路径部分
            exe_in_reg = val.strip().split('"')[1] if '"' in val else val.split()[0]
            return Path(exe_in_reg).resolve() == Path(_exe_path()).resolve()
    except (OSError, IndexError, ValueError):
        return False


def set_auto_start(enable):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            winreg.SetValueEx(key, _REG_VALUE_NAME, 0, winreg.REG_SZ, '"{}" --tray'.format(_exe_path()))
        else:
            try:
                winreg.DeleteValue(key, _REG_VALUE_NAME)
            except FileNotFoundError:
                pass


_auto_start_checked = [False]


def _toggle_auto_start(icon, item):
    new_state = not is_auto_start_enabled()
    set_auto_start(new_state)
    _auto_start_checked[0] = new_state


def _build_menu():
    import pystray
    from pystray import Menu, MenuItem
    _auto_start_checked[0] = is_auto_start_enabled()
    return Menu(
        MenuItem("开机自启", _toggle_auto_start, checked=lambda item: _auto_start_checked[0]),
        MenuItem("查看日志", show_log_window, default=True),
        MenuItem("退出", quit_app),
    )


_single_instance_mutex = None


def check_single_instance():
    """Return True if this is the only instance, False if another is already running."""
    global _single_instance_mutex
    _single_instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "CampusAutoLogin_SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # 183 = ERROR_ALREADY_EXISTS


def run_tray_mode(args):
    """Entry point for tray mode: hide console, show tray icon, run login loop in background."""
    import pystray

    hide_console_window()

    tray_icon_img = create_tray_icon_image()
    icon = pystray.Icon("campus-auto-login", tray_icon_img, "校园网自动登录", _build_menu())

    def login_loop():
        try:
            config = read_config(args.config)
        except Exception as exc:
            write_log(args.log, "配置读取失败:{0}: {1}".format(type(exc).__name__, exc))
            return
        if args.portal_base != DEFAULT_PORTAL:
            config["portal_base"] = args.portal_base.rstrip("/")
        write_log(args.log, "校园网自动登录已在后台启动，监控间隔={0}s.".format(args.interval))
        write_log(args.log, "Portal requests use raw http.client direct transport to bypass system proxy.")
        # Portal auto-discovery at startup
        discovered = discover_portal_base(
            config["portal_base"], timeout=3,
            log_fn=lambda msg: write_log(args.log, msg),
        )
        if discovered.rstrip("/") != config["portal_base"].rstrip("/"):
            write_log(args.log, "Auto-discovered portal: {0}".format(discovered))
            config["portal_base"] = discovered
        failure_state = {"consecutive_failures": 0}
        while True:
            try:
                login_once(config, args, failure_state=failure_state)
            except Exception as exc:
                write_log(args.log, "登录异常（{0}），{1}秒后重试".format(exc, args.interval))
            time.sleep(args.interval)

    login_thread = threading.Thread(target=login_loop, daemon=True)
    login_thread.start()

    tray_thread = threading.Thread(target=icon.run, daemon=True)
    tray_thread.start()

    import tkinter as tk
    global _tk_root
    _tk_root = tk.Tk()
    _tk_root.withdraw()

    def check_show_log():
        if _show_log_event.is_set():
            _show_log_event.clear()
            _create_log_window()
        _tk_root.after(200, check_show_log)

    _tk_root.after(200, check_show_log)
    _tk_root.mainloop()


def main():
    args = parse_args()
    requested_interval = args.interval
    args.interval = normalize_interval(args.interval)

    # Ensure portal hosts bypass any system proxy at the process level
    ensure_process_proxy_bypass_for_portal()

    if args.init:
        init_config(args)
        if not args.once and not args.check and not args.tray:
            return 0
    if args.check:
        return check_only(args)

    if args.check_wifi:
        ssid = get_current_wifi_ssid()
        write_log(args.log, "Current Wi-Fi SSID: {0}".format(ssid or "<not connected>"))
        if args.campus_ssid:
            check_wifi_and_warn(args.campus_ssid, log_fn=lambda msg: write_log(args.log, msg))
        return 0

    if args.set_campus_ssid:
        ssid = get_current_wifi_ssid()
        if not ssid:
            write_log(args.log, "ERROR: No Wi-Fi SSID detected. Connect to campus Wi-Fi first.")
            return 1
        write_log(args.log, "Current Wi-Fi SSID: {0}".format(ssid))
        write_log(args.log, "To save this as campus SSID, run:")
        write_log(args.log, '  campus_auto_login_cli.exe --init --campus-ssid "{0}"'.format(ssid))
        write_log(args.log, "Or add to config manually: \"campus_ssid\": \"{0}\"".format(ssid))
        return 0

    if args.diagnose:
        lines = diagnose_portal_connectivity(args.portal_base)
        for line in lines:
            write_log(args.log, line)
        # Also test resilient fetch layers
        write_log(args.log, "--- Resilient Transport Test ---")
        test_url = "{0}/drcom/chkstatus?callback=_diag&jsVersion=4.X&v=1&lang=zh".format(args.portal_base.rstrip("/"))
        try:
            content, layer = fetch_portal_text_resilient(
                test_url, timeout=5, purpose="diagnose",
                allow_proxy_bypass=args.allow_temporary_proxy_bypass,
            )
            write_log(args.log, "Resilient fetch SUCCESS via layer: {0}".format(layer))
        except OSError as exc:
            write_log(args.log, "Resilient fetch FAILED: {0}".format(exc))
        # Portal auto-discovery
        write_log(args.log, "--- Portal Auto-Discovery ---")
        discovered = discover_portal_base(
            args.portal_base, timeout=3,
            log_fn=lambda msg: write_log(args.log, msg),
        )
        write_log(args.log, "Discovered portal: {0}".format(discovered))
        write_log(args.log, "--- End Discovery ---")
        return 0

    if args.once:
        config = read_config(args.config)
        if args.portal_base != DEFAULT_PORTAL:
            config["portal_base"] = args.portal_base.rstrip("/")
        campus_ssid = args.campus_ssid or config.get("campus_ssid", "")
        if campus_ssid:
            check_wifi_and_warn(campus_ssid, log_fn=lambda msg: write_log(args.log, msg))
        # Portal auto-discovery for --once mode
        discovered = discover_portal_base(
            config["portal_base"], timeout=3,
            log_fn=lambda msg: write_log(args.log, msg),
        )
        if discovered.rstrip("/") != config["portal_base"].rstrip("/"):
            write_log(args.log, "Auto-discovered portal: {0}".format(discovered))
            config["portal_base"] = discovered
        # Wait for portal to become ready (--once can wait longer)
        status = wait_for_portal_ready(
            config["portal_base"], timeout_seconds=60, interval=5,
            log_fn=lambda msg: write_log(args.log, msg),
            allow_proxy_bypass=args.allow_temporary_proxy_bypass,
        )
        if status is None:
            write_log(args.log, "Portal not reachable after 60s. Campus network may not be connected.")
            diagnose_portal_connectivity(config["portal_base"], log_fn=lambda msg: write_log(args.log, msg))
            return 1
        if status["online"]:
            write_log(args.log, "Already online. No login needed.")
            return 0
        # Portal reachable and offline - attempt login
        write_log(args.log, "Campus portal is reachable and account is offline. Trying login...")
        return 0 if login_once(config, args) else 1

    if not args.tray and not args.init and not args.once and not args.check:
        args.tray = True

    if args.tray:
        if not check_single_instance():
            ctypes.windll.user32.MessageBoxW(
                0, "校园网自动登录已在运行中，请勿重复启动。", "Campus Auto Login", 0x40
            )
            return 0
        run_tray_mode(args)
        return 0

    config = read_config(args.config)
    if args.portal_base != DEFAULT_PORTAL:
        config["portal_base"] = args.portal_base.rstrip("/")

    if requested_interval != args.interval:
        write_log(
            args.log,
            "Interval adjusted from {0}s to {1}s.".format(requested_interval, args.interval),
        )
    write_log(args.log, "Campus portal monitor started.Interval={0}s.".format(args.interval))
    while True:
        try:
            login_once(config, args)
        except Exception as exc:
            write_log(args.log, "Login exception:{0}, retrying in {1}s.".format(exc, args.interval))
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        write_log(DEFAULT_LOG, "Stopped by user.")
        raise SystemExit(130)
    except Exception as exc:
        write_log(DEFAULT_LOG, "Fatal error:{0}: {1}".format(type(exc).__name__, exc))
        if getattr(sys, "frozen", False):
            time.sleep(15)
        raise SystemExit(1)
