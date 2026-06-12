#!/usr/bin/env python3
import argparse
import atexit
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
import subprocess
import sys
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path
from urllib import parse, request


DEFAULT_PORTAL = "http://10.200.84.3"
APP_NAME = "YAU-AutoNet-Connect"
APP_VERSION = "1.0.8"
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
        or "Remote end closed connection without response" in msg
        or "Connection reset by peer" in msg
        or "[Errno 104]" in msg  # ECONNRESET
        or "[Errno 10054]" in msg  # WSAECONNRESET on Windows
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

# IP ranges that must never be used as source address for portal connections
_VIRTUAL_IP_PREFIXES = ("198.18.", "198.19.", "169.254.", "127.")

_preferred_source_ip = [None]  # cached after first successful interface-bound connection


def _is_virtual_ip(ip):
    """Return True if the IP belongs to a virtual/TUN/proxy adapter range."""
    return any(ip.startswith(pfx) for pfx in _VIRTUAL_IP_PREFIXES)


def _run_cmd_hidden(args, timeout=15):
    """Run a command with CREATE_NO_WINDOW to avoid console flash."""
    try:
        result = subprocess.run(
            args, capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return _decode_command_output(result.stdout)
    except Exception:
        return ""


def _run_powershell_hidden(script, timeout=15):
    """Run a PowerShell script hidden, return stdout string."""
    return _run_cmd_hidden(
        ["powershell.exe", "-NoProfile", "-Command", script],
        timeout=timeout,
    )


def _get_physical_adapter_ips():
    """Get IPv4 addresses from physical network adapters, excluding virtual ones.
    Returns list of (ip_address, ifIndex, alias, description) tuples."""
    results = []
    try:
        out = _run_powershell_hidden(
            'Get-NetAdapter | Where-Object {$_.Status -eq \'Up\'} | ForEach-Object { '
            '$ip = Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | '
            'Where-Object {$_.IPAddress -ne \'127.0.0.1\' -and $_.IPAddress -notlike \'169.254.*\'} | Select-Object -First 1; '
            'if ($ip) { \'{0}|{1}|{2}|{3}\' -f $ip.IPAddress, $_.ifIndex, $_.Name, $_.InterfaceDescription }'
        ).strip()
        for line in out.splitlines():
            if "|" not in line:
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            ip, ifidx, name, desc = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            combined = (name + " " + desc).lower()
            is_virtual = any(kw in combined for kw in _VIRTUAL_KEYWORDS_NET) or _is_virtual_ip(ip)
            results.append((ip, ifidx, name, desc, is_virtual))
    except Exception:
        pass
    return results


def _find_preferred_source_ip(portal_host="10.200.84.3", portal_port=80, timeout=3):
    """Find the best local IP to bind for reaching the portal.
    Tests each physical adapter's IP with source_address binding.
    Never selects virtual/TUN adapter IPs (198.18.x.x etc).
    Returns (ip, ifidx, alias) or None."""
    # Validate cached value is not a virtual IP
    if _preferred_source_ip[0]:
        cached_ip = _preferred_source_ip[0][0]
        if not _is_virtual_ip(cached_ip):
            return _preferred_source_ip[0]
        # Cached IP is virtual (e.g. 198.18.x.x from TUN) - discard it
        _preferred_source_ip[0] = None

    adapters = _get_physical_adapter_ips()
    # Only use physical adapters - never fall back to virtual
    physical = [(ip, ifidx, name, desc) for ip, ifidx, name, desc, virt in adapters if not virt]

    for ip, ifidx, name, desc in physical:
        if _is_virtual_ip(ip):
            continue
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
    """Fetch URL using PowerShell's .NET WebClient with proxy explicitly bypassed.
    Uses a temp .ps1 file to avoid shell interpretation of URL characters like &."""
    import tempfile
    # Build PowerShell script that outputs Base64-encoded response body
    escaped_url = url.replace("'", "''")
    ps_script = (
        "[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12\n"
        "try {\n"
        "    $wc = New-Object System.Net.WebClient\n"
        "    $wc.Proxy = $null\n"
        "    $wc.Headers.Add('User-Agent', 'campus-auto-login')\n"
        "    $body = $wc.DownloadString('" + escaped_url + "')\n"
        "    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)\n"
        "    [Convert]::ToBase64String($bytes)\n"
        "} catch {\n"
        "    Write-Error $_.Exception.Message\n"
        "    exit 1\n"
        "}\n"
    )
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".ps1", delete=False, mode="w", encoding="utf-8")
        tmp.write(ps_script)
        tmp.close()
        tmp_path = tmp.name
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp_path],
            capture_output=True, timeout=timeout + 5, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode != 0:
            return None
        if stderr and not stdout:
            return None
        if not stdout:
            return None
        # Validate stdout is valid Base64
        try:
            decoded = base64.b64decode(stdout).decode("utf-8", errors="replace")
        except Exception:
            return None
        if not decoded or len(decoded) < 5:
            return None
        # Check for PowerShell error indicators in stderr (not the web page body)
        error_indicators = [
            "CommandNotFoundException", "ParserError", "ParentContainsErrorRecordException",
            "不是内部或外部命令", "所在位置", "FullyQualifiedErrorId",
        ]
        for indicator in error_indicators:
            if indicator in stderr:
                return None
        return decoded
    except (subprocess.TimeoutExpired, Exception):
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


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


# Cached campus network route info for route repair
_campus_route_cache = {"ifIndex": None, "gateway": None, "source_ip": None, "metric": None}


def _load_campus_route_cache():
    """Load the last known campus route from disk into memory."""
    try:
        if not ROUTE_CACHE.exists():
            return None
        with ROUTE_CACHE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("gateway"):
            return None
        _campus_route_cache["ifIndex"] = data.get("ifIndex")
        _campus_route_cache["gateway"] = data.get("gateway")
        _campus_route_cache["source_ip"] = data.get("source_ip")
        _campus_route_cache["metric"] = data.get("metric")
        return data
    except Exception:
        return None


def _save_campus_route_cache(info):
    """Persist non-secret campus route hints for later reconnect attempts."""
    try:
        previous = _load_campus_route_cache() or {}
        current_ssid = get_current_wifi_ssid()
        if not current_ssid:
            current_ssid = str(previous.get("ssid") or "").strip()
        if not current_ssid or current_ssid == "?":
            current_ssid = _cached_or_configured_campus_ssid()
        data = {
            "ifIndex": info.get("ifIndex"),
            "gateway": info.get("nextHop") or info.get("gateway"),
            "source_ip": info.get("sourceIP") or info.get("source_ip"),
            "metric": info.get("metric"),
            "alias": info.get("alias"),
            "ssid": current_ssid,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if not data["gateway"]:
            return False
        with ROUTE_CACHE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _cache_campus_route(portal_base):
    """Cache the current route info when portal is reachable, for later route repair."""
    host = parse.urlsplit(portal_base.rstrip("/")).hostname or "10.200.84.3"
    info = _get_portal_route_info(host)
    if not info.get("nextHop"):
        default_gw = _get_default_gateway()
        if default_gw:
            info["nextHop"] = default_gw
    if not info.get("sourceIP"):
        src = _find_preferred_source_ip(host, 80, timeout=1)
        if src:
            info["sourceIP"], info["ifIndex"], info["alias"] = src[0], src[1], src[2]
    if info.get("nextHop"):
        _campus_route_cache["ifIndex"] = info.get("ifIndex")
        _campus_route_cache["gateway"] = info.get("nextHop")
        _campus_route_cache["source_ip"] = info.get("sourceIP")
        _campus_route_cache["metric"] = info.get("metric")
        _save_campus_route_cache(info)


def _try_route_repair(portal_host="10.200.84.3", timeout=3):
    """Try to add a temporary host route to the portal using cached campus route info.
    Returns True if portal becomes reachable after route repair."""
    if not _campus_route_cache.get("gateway"):
        _load_campus_route_cache()
    gw = _campus_route_cache.get("gateway")
    ifidx = _campus_route_cache.get("ifIndex")
    if not gw:
        return False
    # Only add a /32 host route for the specific portal IP
    route_args = ["route", "add", portal_host, "mask", "255.255.255.255", gw]
    if ifidx:
        route_args += ["if", str(ifidx)]
    try:
        result = _run_cmd_hidden(route_args, timeout=10)
        if "The requested operation requires elevation" in result or "Access is denied" in result:
            return False
        # Test if portal is now reachable
        time.sleep(0.5)
        try:
            s = socket.create_connection((portal_host, 80), timeout=timeout)
            s.close()
            return True
        except OSError:
            return False
    except Exception:
        return False


def _cleanup_route_repair(portal_host="10.200.84.3"):
    """Remove temporary host route added by _try_route_repair."""
    try:
        _run_cmd_hidden(["route", "delete", portal_host], timeout=10)
    except Exception:
        pass


def fetch_portal_text_resilient(url, headers=None, timeout=10, purpose="status",
                                 allow_proxy_bypass=False):
    """Resilient multi-layer fetch for campus portal URLs.
    Layer 1: raw http.client direct
    Layer 2: interface-bound raw direct (bind to physical adapter IP)
    Layer 3: temporary host route repair (if cached campus route exists)
    Layer 4: PowerShell .NET WebClient no proxy (EncodedCommand)
    Layer 5: temporary Windows proxy bypass (if allowed)

    Returns (text, layer_name) on success, raises OSError if all layers fail.
    """
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-python", "Accept": "*/*"}

    parsed = parse.urlsplit(url)
    portal_host = parsed.hostname or "10.200.84.3"
    portal_port = parsed.port or 80
    errors = []

    # Layer 1: raw http.client direct
    try:
        text = fetch_direct_text(url, headers=headers, timeout=timeout)
        return text, "raw_direct"
    except (OSError, ValueError) as e:
        errors.append("L1_raw_direct: {0}".format(e))

    # Layer 2a: cached source-IP raw direct. This covers short windows where
    # Windows still has a campus route hint but adapter enumeration is stale.
    _load_campus_route_cache()
    cached_source_ip = _campus_route_cache.get("source_ip")
    if cached_source_ip and not _is_virtual_ip(cached_source_ip):
        try:
            text = fetch_direct_with_source(url, cached_source_ip, headers=headers, timeout=timeout)
            _preferred_source_ip[0] = (
                cached_source_ip,
                _campus_route_cache.get("ifIndex"),
                _campus_route_cache.get("alias") or "cached",
            )
            return text, "cached_source({0})".format(cached_source_ip)
        except (OSError, ValueError) as e:
            errors.append("L2_cached_source({0}): {1}".format(cached_source_ip, e))

    # Layer 2b: interface-bound raw direct
    src = _find_preferred_source_ip(portal_host, portal_port, timeout=min(timeout, 5))
    if src:
        try:
            text = fetch_direct_with_source(url, src[0], headers=headers, timeout=timeout)
            return text, "interface_bound({0})".format(src[0])
        except (OSError, ValueError) as e:
            errors.append("L2_interface_bound({0}): {1}".format(src[0], e))
    else:
        errors.append("L2_interface_bound: no local interface can reach portal")

    # Layer 3: temporary host route repair
    if not _campus_route_cache.get("gateway"):
        _load_campus_route_cache()
    if _campus_route_cache.get("gateway"):
        route_added = _try_route_repair(portal_host, timeout=min(timeout, 5))
        if route_added:
            try:
                text = fetch_direct_text(url, headers=headers, timeout=timeout)
                return text, "route_repair"
            except (OSError, ValueError) as e:
                errors.append("L3_route_repair: route added but fetch failed: {0}".format(e))
            finally:
                _cleanup_route_repair(portal_host)
        else:
            errors.append("L3_route_repair: could not repair route")
    else:
        errors.append("L3_route_repair: no cached campus route")

    # Layer 4: PowerShell .NET WebClient no proxy (EncodedCommand)
    ps_result = _powershell_no_proxy_fetch(url, timeout=min(timeout, 15))
    if ps_result:
        return ps_result, "powershell_no_proxy"
    else:
        errors.append("L4_powershell_no_proxy: failed")

    # Layer 5: temporary proxy bypass (only if allowed)
    if allow_proxy_bypass:
        text = _temporary_proxy_bypass_fetch(url, headers=headers, timeout=timeout)
        if text:
            return text, "temp_proxy_bypass"
        errors.append("L5_temp_proxy_bypass: failed")
    else:
        errors.append("L5_temp_proxy_bypass: not enabled")

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
ROUTE_CACHE = SCRIPT_DIR / "campus_route_cache.json"
MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 30

_log_queue = queue.Queue(maxsize=500)  # bounded to prevent unbounded growth if log window never opened
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
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # disk full, permissions, etc. - don't crash the monitoring loop
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
    if force_trailing_lang:
        all_params = [(k, v) for k, v in all_params if k != "lang"]
        all_params.append(("lang", "zh"))
    elif "lang" not in keys:
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
    if force_trailing_lang:
        all_params = [(k, v) for k, v in all_params if k != "lang"]
        all_params.append(("lang", "zh"))
    elif "lang" not in keys:
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
        raise FileNotFoundError("Config not found. Run: campus_auto_login_cli.exe --init")
    with config_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not data.get("username") or not data.get("password_dpapi"):
        raise ValueError("Config misses username or password_dpapi. Run: campus_auto_login_cli.exe --init")
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


def _has_console_stdin():
    """Check if stdin is connected to a real console (not piped or absent)."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, OSError):
        return False


def _init_config_gui():
    """Use tkinter dialogs to collect init config when no console is available."""
    try:
        import tkinter as tk
        from tkinter import simpledialog, messagebox
    except ImportError as exc:
        # tkinter not available - write error to log
        try:
            log_path = SCRIPT_DIR / "campus_auto_login_py.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write("[{0}] GUI init failed: {1}\n".format(
                    time.strftime("%Y-%m-%d %H:%M:%S"), exc))
                f.write("Please use campus_auto_login_cli.exe --init instead.\n")
        except Exception:
            pass
        return None

    root = tk.Tk()
    root.withdraw()

    username = simpledialog.askstring("校园网自动登录 - 初始化", "校园网用户名:", parent=root)
    if not username:
        messagebox.showinfo("取消", "初始化已取消。", parent=root)
        root.destroy()
        return None

    password = simpledialog.askstring("校园网自动登录 - 初始化", "校园网密码:", show="*", parent=root)
    if not password:
        messagebox.showinfo("取消", "初始化已取消。", parent=root)
        root.destroy()
        return None

    suffix = simpledialog.askstring(
        "校园网自动登录 - 初始化",
        "运营商后缀:\n直接回车为默认\n@dx 电信\n@lt 联通",
        parent=root,
    )
    if suffix is None:
        suffix = ""

    root.destroy()
    return username, password, suffix


def init_config(args):
    if _has_console_stdin():
        username = input("Campus username:")
        password = getpass.getpass("Campus password:")
        suffix = input("Service suffix(empty for default,@dx for telecom,@lt for unicom):")
    else:
        result = _init_config_gui()
        if result is None:
            return
        username, password, suffix = result

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
    if _has_console_stdin():
        write_log(args.log, "Config created:{0}".format(args.config))
    else:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("初始化完成", "配置已保存到:\n{0}\n\n双击 campus_auto_login.exe 即可使用。".format(args.config), parent=root)
        root.destroy()


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
        try:
            result_val = int(data.get("result", 0))
        except (TypeError, ValueError):
            result_val = 0
        return {
            "state": "online" if result_val == 1 else "offline",
            "reachable": True,
            "online": result_val == 1,
            "raw": data,
            "error": None,
            "layer": layer,
            "attempts": 1,
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
            "attempts": 1,
        }


_last_discovery_time = 0
_DISCOVERY_COOLDOWN = 300  # 5 minutes between portal discoveries

# Cached login params from last successful login (for fast retry)
_cached_login_params = None  # dict with account, password, wlan_user_ip, wlan_user_mac, terminal_type, portal_base


def login_once(config, args, failure_state=None):
    """Attempt one login cycle. Returns True if online after this call.
    failure_state: dict to track consecutive failures across calls (for tray loop).
    """
    global _cached_login_params, _last_discovery_time
    allow_bypass = getattr(args, "allow_temporary_proxy_bypass", False)
    status = get_status(config["portal_base"], allow_proxy_bypass=allow_bypass)

    if status["state"] == "network_not_ready":
        # Socket-level failure - transient, likely Wi-Fi roaming or network transition
        # Fast retry: try cached login params directly (skip status query)
        if _cached_login_params and _cached_login_params.get("portal_base") == config["portal_base"]:
            try:
                cached = _cached_login_params
                fast_params = [
                    ("login_method", "1"),
                    ("user_account", cached["account"]),
                    ("user_password", cached["password"]),
                    ("wlan_user_ip", cached["wlan_user_ip"]),
                    ("wlan_user_ipv6", ""),
                    ("wlan_user_mac", cached["wlan_user_mac"]),
                    ("wlan_ac_ip", ""),
                    ("wlan_ac_name", ""),
                    ("jsVersion", "4.2.1"),
                    ("terminal_type", cached["terminal_type"]),
                    ("lang", "zh-cn"),
                ]
                result = invoke_url_jsonp(
                    eportal_login_url(config["portal_base"]),
                    fast_params, timeout=8,
                    force_trailing_lang=True,
                    portal_base=config["portal_base"],
                    allow_proxy_bypass=allow_bypass,
                )
                if result.get("result") == 1 or str(result.get("result", "")).lower() in {"1", "ok"}:
                    time.sleep(1)
                    check = get_status(config["portal_base"], allow_proxy_bypass=allow_bypass)
                    if check["online"]:
                        write_log(args.log, "缓存参数快速登录成功")
                        return True
            except Exception:
                pass  # fall through to normal recovery

        if failure_state is not None:
            failure_state["consecutive_failures"] = failure_state.get("consecutive_failures", 0) + 1

        write_log(args.log, "网络中断，恢复中...")
        # Immediately request Wi-Fi reconnect (always try, even without configured SSID)
        campus_ssid = getattr(args, "campus_ssid", "") or config.get("campus_ssid", "")
        reconnect_campus_wifi(campus_ssid, log_fn=lambda msg: write_log(args.log, msg))
        # Wait for Wi-Fi to reconnect, then quick retry
        time.sleep(5)
        retry = get_status(config["portal_base"], allow_proxy_bypass=allow_bypass)
        if retry["state"] in ("online", "offline"):
            write_log(args.log, "快速恢复成功")
            status = retry
        else:
            # Still down, let the normal 10s loop handle further recovery
            return False

    if status["state"] == "portal_unreachable":
        write_log(args.log, "portal访问异常，尝试发现其他地址...")
        if failure_state is not None:
            failure_state["consecutive_failures"] = failure_state.get("consecutive_failures", 0) + 1
        # Try portal auto-discovery (rate-limited)
        if time.time() - _last_discovery_time > _DISCOVERY_COOLDOWN:
            _last_discovery_time = time.time()
            discovered = discover_portal_base(
                config["portal_base"], timeout=3,
                log_fn=lambda msg: write_log(args.log, msg),
            )
        else:
            discovered = config["portal_base"]
        if discovered.rstrip("/") != config["portal_base"].rstrip("/"):
            write_log(args.log, "切换到发现的portal: {0}".format(discovered))
            config["portal_base"] = discovered
            status = get_status(config["portal_base"], allow_proxy_bypass=allow_bypass)
            if not status["reachable"]:
                return False
        else:
            return False

    # Portal is reachable - reset failure counter and cache route for future repair
    if failure_state is not None:
        failure_state["consecutive_failures"] = 0
    _cache_campus_route(config["portal_base"])

    if status["online"]:
        write_log(args.log, "已连接 | {0}".format(status.get("layer", "direct")))
        return True
    write_log(args.log, "portal可达，账号未认证，尝试登录...")
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
                time.sleep(2)
                after = get_status(config["portal_base"], allow_proxy_bypass=allow_bypass)
                if after["online"]:
                    write_log(args.log, "登录成功，已上线")
                    # Cache login params for fast retry on next disconnection
                    _cached_login_params = {
                        "account": account,
                        "password": password,
                        "wlan_user_ip": wlan_user_ip,
                        "wlan_user_mac": wlan_user_mac,
                        "terminal_type": terminal_type,
                        "portal_base": config["portal_base"],
                    }
                    return True
                write_log(args.log, "登录请求成功但复查未上线")
            else:
                message = (
                    result.get("msg")
                    or result.get("error_msg")
                    or result.get("ErrorMsg")
                    or result.get("ret_code")
                    or result.get("result")
                    or "unknown error"
                )
                write_log(args.log, "登录失败: {0}".format(message))
            if index < args.max_attempts:
                time.sleep(args.retry_seconds)
        return False
    finally:
        password = None
        if _cached_login_params is not None:
            _cached_login_params["password"] = None


def check_only(args):
    allow_bypass = getattr(args, "allow_temporary_proxy_bypass", False)
    status = get_status(args.portal_base, allow_proxy_bypass=allow_bypass)
    if not status["reachable"]:
        write_log(args.log, "portal不可达: {0}".format(status["error"]))
        return 2
    if status["online"]:
        write_log(args.log, "已连接")
        return 0
    write_log(args.log, "未认证")
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
    parser.add_argument("--force-portal-reachable", action="store_true",
                        help="Force all transport layers to try reaching the portal.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# WiFi SSID detection
# ---------------------------------------------------------------------------

def _decode_command_output(data):
    """Decode netsh/PowerShell output across common Windows encodings."""
    if not data:
        return ""
    if isinstance(data, str):
        return data
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode(errors="replace")


def get_current_wifi_ssid():
    """Get the current WiFi SSID (read-only, best-effort)."""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = _decode_command_output(result.stdout)
        for line in out.splitlines():
            if "SSID" in line and "BSSID" not in line:
                parts = line.split(":", 1)
                if len(parts) >= 2:
                    ssid = parts[1].strip()
                    if ssid and ssid != "?":
                        return ssid
    except Exception:
        pass
    return ""


def _get_wifi_profiles():
    """Return saved Wi-Fi profile names from netsh output."""
    profiles = []
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "profiles"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        text = _decode_command_output(result.stdout)
        for line in text.splitlines():
            if ":" not in line:
                continue
            left, right = line.split(":", 1)
            name = right.strip()
            if name and ("Profile" in left or "配置文件" in left):
                profiles.append(name)
    except Exception:
        pass
    return profiles


def _cached_or_configured_campus_ssid(campus_ssid=""):
    """Return configured SSID or the last cached campus SSID."""
    if campus_ssid:
        return campus_ssid
    cached = _load_campus_route_cache()
    ssid = str((cached or {}).get("ssid") or "").strip()
    if ssid and ssid not in {"?", "<not connected>"}:
        return ssid
    current = get_current_wifi_ssid()
    if current:
        return current
    profiles = _get_wifi_profiles()
    for profile in profiles:
        if profile.lower() == "yadx-stu":
            return profile
    for profile in profiles:
        if "yadx" in profile.lower() or "yau" in profile.lower():
            return profile
    return ""


def _is_wifi_power_off_error(text):
    """Return True when Windows reports WLAN radio/interface power is off."""
    lowered = (text or "").lower()
    return (
        "无线局域网接口电源关闭" in text
        or "wlangetavailablenetworklist" in lowered
        or ("radio" in lowered and "off" in lowered)
        or ("power" in lowered and "off" in lowered)
    )


def _get_wifi_adapter_name():
    """Return the most likely Windows Wi-Fi adapter name."""
    try:
        ps = (
            "$a=Get-NetAdapter | Where-Object {"
            "$_.Name -eq 'WLAN' -or $_.InterfaceDescription -match 'Wi-Fi|Wireless|802\\.11'"
            "} | Sort-Object Status,Name | Select-Object -First 1 -ExpandProperty Name;"
            "if($a){$a}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        name = _decode_command_output(result.stdout).strip().splitlines()
        if name:
            first = name[0].strip()
            if first:
                return first
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        text = _decode_command_output(result.stdout)
        for line in text.splitlines():
            if ":" not in line:
                continue
            left, right = line.split(":", 1)
            if left.strip().lower() == "name" or left.strip() == "名称":
                name = right.strip()
                if name:
                    return name
    except Exception:
        pass
    return "WLAN"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [
        ("InterfaceGuid", _GUID),
        ("strInterfaceDescription", ctypes.c_wchar * 256),
        ("isState", wintypes.DWORD),
    ]


class _WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfItems", wintypes.DWORD),
        ("dwIndex", wintypes.DWORD),
        ("InterfaceInfo", _WLAN_INTERFACE_INFO * 1),
    ]


class _WLAN_PHY_RADIO_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPhyIndex", wintypes.DWORD),
        ("dot11SoftwareRadioState", wintypes.DWORD),
        ("dot11HardwareRadioState", wintypes.DWORD),
    ]


class _WLAN_RADIO_STATE(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfPhys", wintypes.DWORD),
        ("PhyRadioState", _WLAN_PHY_RADIO_STATE * 64),
    ]


def _wlan_error_message(code):
    try:
        return ctypes.FormatError(code).strip()
    except Exception:
        return str(code)


def _query_wifi_phy_indices(wlanapi, client_handle, interface_guid):
    """Return PHY indices for an interface before setting software radio state."""
    data_size = wintypes.DWORD()
    opcode_type = wintypes.DWORD()
    data_ptr = ctypes.c_void_p()
    result = wlanapi.WlanQueryInterface(
        client_handle,
        ctypes.byref(interface_guid),
        4,  # wlan_intf_opcode_radio_state
        None,
        ctypes.byref(data_size),
        ctypes.byref(data_ptr),
        ctypes.byref(opcode_type),
    )
    if result != 0 or not data_ptr.value:
        return [0]
    try:
        radio_state = ctypes.cast(data_ptr, ctypes.POINTER(_WLAN_RADIO_STATE)).contents
        count = min(int(radio_state.dwNumberOfPhys), 64)
        indices = []
        for i in range(count):
            indices.append(int(radio_state.PhyRadioState[i].dwPhyIndex))
        return indices or [0]
    finally:
        wlanapi.WlanFreeMemory(data_ptr)


def _enable_wifi_software_radio(log_fn=None):
    """Turn on Wi-Fi software radio through Native Wi-Fi API when available."""
    client_handle = wintypes.HANDLE()
    negotiated_version = wintypes.DWORD()
    interface_list = ctypes.POINTER(_WLAN_INTERFACE_INFO_LIST)()
    try:
        wlanapi = ctypes.WinDLL("wlanapi")
        wlanapi.WlanOpenHandle.restype = wintypes.DWORD
        wlanapi.WlanEnumInterfaces.restype = wintypes.DWORD
        wlanapi.WlanSetInterface.restype = wintypes.DWORD
        wlanapi.WlanQueryInterface.restype = wintypes.DWORD
        result = wlanapi.WlanOpenHandle(2, None, ctypes.byref(negotiated_version), ctypes.byref(client_handle))
        if result != 0:
            if log_fn:
                log_fn("Wi-Fi API打开失败: {0}".format(_wlan_error_message(result)))
            return False
        result = wlanapi.WlanEnumInterfaces(client_handle, None, ctypes.byref(interface_list))
        if result != 0 or not interface_list:
            if log_fn:
                log_fn("Wi-Fi接口枚举失败: {0}".format(_wlan_error_message(result)))
            return False
        count = int(interface_list.contents.dwNumberOfItems)
        base = ctypes.addressof(interface_list.contents.InterfaceInfo)
        interfaces = (_WLAN_INTERFACE_INFO * count).from_address(base)
        any_success = False
        for interface_info in interfaces:
            description = interface_info.strInterfaceDescription
            phy_indices = _query_wifi_phy_indices(wlanapi, client_handle, interface_info.InterfaceGuid)
            for phy_index in phy_indices:
                radio_state = _WLAN_PHY_RADIO_STATE(
                    wintypes.DWORD(phy_index),
                    wintypes.DWORD(1),  # dot11_radio_state_on
                    wintypes.DWORD(1),
                )
                result = wlanapi.WlanSetInterface(
                    client_handle,
                    ctypes.byref(interface_info.InterfaceGuid),
                    4,  # wlan_intf_opcode_radio_state
                    ctypes.sizeof(radio_state),
                    ctypes.byref(radio_state),
                    None,
                )
                if result == 0:
                    any_success = True
                elif log_fn:
                    log_fn("Wi-Fi射频开启失败({0}): {1}".format(
                        description or "unknown", _wlan_error_message(result)))
        return any_success
    except Exception as exc:
        if log_fn:
            log_fn("Wi-Fi射频开启失败: {0}".format(exc))
        return False
    finally:
        try:
            if interface_list:
                ctypes.WinDLL("wlanapi").WlanFreeMemory(interface_list)
        except Exception:
            pass
        try:
            if client_handle:
                ctypes.WinDLL("wlanapi").WlanCloseHandle(client_handle, None)
        except Exception:
            pass


def ensure_wifi_interface_enabled(log_fn=None):
    """Best-effort enable of the Windows Wi-Fi adapter/autoconfig service."""
    adapter_name = _get_wifi_adapter_name()
    if not adapter_name:
        return False
    if log_fn:
        log_fn("尝试启用Wi-Fi接口: {0}".format(adapter_name))
    commands = [
        ["netsh", "interface", "set", "interface", "name={0}".format(adapter_name), "admin=enabled"],
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Enable-NetAdapter -Name '{0}' -Confirm:$false -ErrorAction SilentlyContinue".format(
                adapter_name.replace("'", "''")
            ),
        ],
        ["netsh", "wlan", "set", "autoconfig", "enabled=yes", "interface={0}".format(adapter_name)],
    ]
    any_success = False
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode == 0:
                any_success = True
            elif log_fn:
                err = _decode_command_output(result.stderr or result.stdout).strip()
                if err:
                    log_fn("Wi-Fi启用失败: {0}".format(err))
        except Exception as exc:
            if log_fn:
                log_fn("Wi-Fi启用失败: {0}".format(exc))
    if _enable_wifi_software_radio(log_fn=log_fn):
        any_success = True
    return any_success


def disable_wifi_power_save(log_fn=None):
    """Disable Wi-Fi adapter power saving to prevent disconnection during lock screen.
    Uses PowerShell Set-NetAdapterPowerManagement to disable DeviceSleepOnDisconnect
    and WakeOnMagicPacket. Also disables 'Allow the computer to turn off this device
    to save power' via WMI.
    Best-effort: returns True if any command succeeded."""
    adapter_name = _get_wifi_adapter_name()
    if not adapter_name:
        return False
    if log_fn:
        log_fn("禁用Wi-Fi省电模式: {0}".format(adapter_name))
    any_success = False
    # Disable power management via Set-NetAdapterPowerManagement (Windows 8+)
    ps_disable_pm = (
        "$a = '{0}'; "
        "try {{ "
        "  $pm = Get-NetAdapterPowerManagement -Name $a -ErrorAction Stop; "
        "  $pm.DeviceSleepOnDisconnect = 0; "
        "  $pm.WakeOnMagicPacket = 1; "
        "  $pm.WakeOnPattern = 1; "
        "  Set-NetAdapterPowerManagement -InputObject $pm -ErrorAction SilentlyContinue; "
        "  Write-Output 'PM_SET_OK' "
        "}} catch {{ Write-Output 'PM_SET_SKIP' }}"
    ).format(adapter_name.replace("'", "''"))
    out = _run_powershell_hidden(ps_disable_pm, timeout=15).strip()
    if "PM_SET_OK" in out:
        any_success = True
        if log_fn:
            log_fn("Wi-Fi省电模式已禁用")
    # Disable 'Allow the computer to turn off this device to save power' via WMI
    ps_disable_wmi = (
        "$a = '{0}'; "
        "$nics = Get-WmiObject -Class MSPower_DeviceEnable -Namespace root\\wmi -ErrorAction SilentlyContinue; "
        "if ($nics) {{ "
        "  foreach ($nic in $nics) {{ "
        "    if ($nic.InstanceName -match 'Wireless|Wi-Fi|802\\.11|WLAN') {{ "
        "      $nic.Enable = $false; $nic.Put() | Out-Null; "
        "      Write-Output 'WMI_SET_OK' "
        "    }} "
        "  }} "
        "}}"
    ).format(adapter_name.replace("'", "''"))
    out2 = _run_powershell_hidden(ps_disable_wmi, timeout=15).strip()
    if "WMI_SET_OK" in out2:
        any_success = True
    # Also disable via netsh (some adapters respond to this)
    _run_cmd_hidden(
        ["netsh", "wlan", "set", "autoconfig", "enabled=yes", "interface={0}".format(adapter_name)],
        timeout=10,
    )
    return any_success


# ---------------------------------------------------------------------------
# System sleep prevention (SetThreadExecutionState)
# ---------------------------------------------------------------------------

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040
_sleep_prevention_active = False


def _prevent_system_sleep():
    """Tell Windows to keep the system awake (prevent Modern Standby / connected standby).
    Does NOT prevent screen from turning off. Reversible with _restore_system_sleep."""
    global _sleep_prevention_active
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED
        )
        _sleep_prevention_active = True
    except Exception:
        pass


def _restore_system_sleep():
    """Release the sleep prevention, allowing Windows to enter low-power states."""
    global _sleep_prevention_active
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        _sleep_prevention_active = False
    except Exception:
        pass


def _run_netsh_wifi_connect(ssid):
    return subprocess.run(
        ["netsh", "wlan", "connect", "name={0}".format(ssid)],
        capture_output=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def reconnect_campus_wifi(campus_ssid="", log_fn=None):
    """Reconnect the known campus Wi-Fi profile with netsh."""
    ssid = _cached_or_configured_campus_ssid(campus_ssid)
    if not ssid:
        return False
    try:
        if log_fn:
            log_fn('正在重连Wi-Fi: {0}'.format(ssid))
        result = _run_netsh_wifi_connect(ssid)
        if result.returncode == 0:
            if log_fn:
                log_fn('Wi-Fi重连已请求: {0}'.format(ssid))
            return True
        err = _decode_command_output(result.stderr or result.stdout).strip()
        if _is_wifi_power_off_error(err):
            if log_fn:
                log_fn("Wi-Fi接口已关闭，尝试启用...")
            ensure_wifi_interface_enabled(log_fn=log_fn)
            time.sleep(3)
            result = _run_netsh_wifi_connect(ssid)
            if result.returncode == 0:
                if log_fn:
                    log_fn('Wi-Fi重连已请求: {0}'.format(ssid))
                return True
            err = _decode_command_output(result.stderr or result.stdout).strip()
        if log_fn:
            log_fn('Wi-Fi重连失败: {0}'.format(err or result.returncode))
    except Exception as exc:
        if log_fn:
            log_fn('Wi-Fi重连失败: {0}'.format(exc))
    return False


def check_wifi_and_warn(campus_ssid, log_fn=None):
    """Check if current WiFi matches campus SSID. Returns True if OK."""
    current = get_current_wifi_ssid()
    if not current:
        return True  # can't determine, don't block
    if not campus_ssid:
        return True
    if current.lower() == campus_ssid.lower():
        return True
    if log_fn:
        log_fn("Wi-Fi不匹配: 当前'{0}'，校园网'{1}'".format(current, campus_ssid))
    return False


def normalize_interval(seconds):
    if seconds < MIN_INTERVAL_SECONDS:
        return MIN_INTERVAL_SECONDS
    if seconds > MAX_INTERVAL_SECONDS:
        return MAX_INTERVAL_SECONDS
    return seconds


# ---------------------------------------------------------------------------
# Boot grace period & network readiness gate
# ---------------------------------------------------------------------------

BOOT_GRACE_SECONDS = 30  # brief wait after boot; wait_for_network_ready handles the rest
_network_ready_logged = False


def _get_system_boot_time():
    """Return the Unix timestamp of the last Windows boot (best-effort)."""
    try:
        out = _run_powershell_hidden(
            '(Get-CimInstance Win32_OperatingSystem).LastBootUpTime | '
            'ForEach-Object { [int]([DateTimeOffset]::new($_).ToUnixTimeSeconds()) }',
            timeout=10,
        ).strip()
        for line in out.splitlines():
            val = line.strip()
            if val.isdigit():
                return int(val)
    except Exception:
        pass
    return 0


def _seconds_since_boot():
    """Return seconds elapsed since system boot, or 0 if unknown."""
    boot_ts = _get_system_boot_time()
    if boot_ts > 0:
        return int(time.time()) - boot_ts
    return 0


def boot_grace_wait(log_fn=None):
    """If system recently booted, wait until network stabilizes.
    Returns True if we had to wait, False if no wait was needed."""
    elapsed = _seconds_since_boot()
    if elapsed <= 0 or elapsed >= BOOT_GRACE_SECONDS:
        return False
    remaining = BOOT_GRACE_SECONDS - elapsed
    if log_fn:
        log_fn("开机初始化中，等待{0}秒网络就绪...".format(remaining))
    time.sleep(remaining)
    return True


def network_ready(portal_host="10.200.84.3", portal_port=80, log_fn=None):
    """Check if the network is ready for portal probing.
    Primary check: TCP connect to portal succeeds = network is ready.
    Secondary info: adapter status, gateway, etc. (logged but not blocking).
    Returns True/False and logs details on first call or state change.
    """
    global _network_ready_logged
    details = []

    # Info: adapter status (non-blocking, for diagnostics only)
    adapters = _get_physical_adapter_ips()
    physical = [(ip, ifidx, name, desc) for ip, ifidx, name, desc, virt in adapters if not virt]
    if not physical:
        details.append("等待物理网卡就绪...")

    # Info: default gateway (non-blocking)
    gw = _get_default_gateway()
    if gw:
        details.append("网关: {0}".format(gw))

    # PRIMARY CHECK: TCP connect to portal
    try:
        s = socket.create_connection((portal_host, portal_port), timeout=3)
        s.close()
        details.append("portal TCP可达")
        ok = True
    except OSError as exc:
        details.append("portal TCP不可达: {0}".format(exc))
        ok = False

    # Log on first call or state change
    if not _network_ready_logged or not ok:
        if log_fn:
            for d in details:
                log_fn(d)
        _network_ready_logged = ok

    return ok


def _has_physical_adapter():
    """Quick check: does at least one non-virtual adapter have an IP?"""
    adapters = _get_physical_adapter_ips()
    return any(not virt for _, _, _, _, virt in adapters)


def wait_for_network_ready(portal_host="10.200.84.3", portal_port=80,
                           timeout_seconds=120, check_interval=3,
                           stable_seconds=5, log_fn=None):
    """Wait until network_ready() returns True for consecutive stable_seconds.
    Extends timeout automatically if system recently booted and no adapter detected.
    Returns True if stable, False if timeout."""
    # Auto-extend timeout for cold boot scenarios
    boot_elapsed = _seconds_since_boot()
    if 0 < boot_elapsed < 300 and not _has_physical_adapter():
        timeout_seconds = max(timeout_seconds, 180)
        if log_fn:
            log_fn("开机初始化，等待网络就绪(最多{0}秒)...".format(timeout_seconds))

    deadline = time.time() + timeout_seconds
    consecutive_ok = 0
    first_pass = True

    while time.time() < deadline:
        if network_ready(portal_host, portal_port, log_fn=log_fn if first_pass else None):
            consecutive_ok += check_interval
            if consecutive_ok >= stable_seconds:
                if log_fn:
                    log_fn("网络就绪")
                return True
        else:
            consecutive_ok = 0
        first_pass = False
        remaining = deadline - time.time()
        time.sleep(min(check_interval, max(1, remaining)))

    if log_fn:
        log_fn("网络就绪等待超时({0}秒)".format(timeout_seconds))
    return False


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
    """Test if a URL is a reachable campus portal by checking /drcom/chkstatus.
    Returns True ONLY if the response is valid JSONP/JSON with a 'result' field."""
    try:
        text = fetch_direct_text(
            "{0}/drcom/chkstatus?callback=_test&jsVersion=4.X&v=1&lang=zh".format(base_url.rstrip("/")),
            headers={"User-Agent": "Mozilla/5.0 campus-auto-login-discovery"},
            timeout=timeout,
        )
        if not text or len(text.strip()) < 5:
            return False
        # Reject obvious error responses
        error_indicators = [
            "CommandNotFoundException", "ParserError", "不是内部或外部命令",
            "ParentContainsErrorRecordException", "FullyQualifiedErrorId",
        ]
        for indicator in error_indicators:
            if indicator in text:
                return False
        obj = jsonp_to_obj(text)
        if isinstance(obj, dict) and "result" in obj:
            return True
    except Exception:
        pass
    return False


def _get_default_gateway():
    """Get the default gateway IP (read-only, best-effort)."""
    try:
        output = _run_powershell_hidden(
            'Get-NetRoute -AddressFamily IPv4 -DestinationPrefix \'0.0.0.0/0\' -ErrorAction SilentlyContinue | '
            'Sort-Object RouteMetric | Select-Object -First 1 | ForEach-Object { $_.NextHop }',
            timeout=10,
        ).strip()
        for line in output.splitlines():
            gw = line.strip()
            if gw and gw != "0.0.0.0" and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", gw):
                return gw
    except Exception:
        pass
    try:
        output = _run_cmd_hidden(["route", "print", "0.0.0.0"], timeout=10)
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
    Returns the working portal base URL ONLY if verified, or configured one as unverified fallback.
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    configured = configured_portal_base.rstrip("/")

    # 1. Try configured portal
    if _test_portal_candidate(configured, timeout=timeout):
        _log("portal确认: {0}".format(configured))
        return configured

    _log("{0}不可达，搜索其他portal...".format(configured))

    # 2. Try DEFAULT_PORTAL if different from configured
    default = DEFAULT_PORTAL.rstrip("/")
    if default != configured and _test_portal_candidate(default, timeout=timeout):
        _log("portal确认(默认): {0}".format(default))
        return default

    # 3. Try gateway subnet (local, no internet needed)
    gateway = _get_default_gateway()
    if gateway:
        for candidate in _get_gateway_subnet_candidates(gateway):
            if candidate.rstrip("/") in (configured, default):
                continue
            if _test_portal_candidate(candidate, timeout=timeout):
                _log("发现portal(网关子网): {0}".format(candidate))
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
                        _log("发现portal(重定向): {0}".format(candidate))
                        return candidate
        except Exception:
            continue

    # No verified portal found - return configured as unverified fallback
    _log("未找到可用portal，使用配置地址: {0}".format(configured))
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
        route_out = _run_cmd_hidden(["route", "print", portal_host], timeout=10)
        in_active = False
        for line in route_out.splitlines():
            if "Active Routes" in line or "活动路由" in line:
                in_active = True
                continue
            if in_active:
                parts = line.split()
                if len(parts) >= 5:
                    dest, mask, gw, iface, metric = parts[0], parts[1], parts[2], parts[3], parts[4]
                    if dest == portal_host or dest == "0.0.0.0":
                        result["nextHop"] = gw if gw.lower() != "on-link" else None
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
            src_ip = result["sourceIP"]
            ps_out = _run_powershell_hidden(
                'Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -eq \'' + src_ip + '\'} | '
                'Select-Object -First 1 | ForEach-Object { \'{0}|{1}|{2}\' -f $_.InterfaceIndex, $_.InterfaceAlias, $_.IPAddress }',
                timeout=10,
            ).strip()
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


def _detect_virtual_adapters():
    """Detect virtual/TUN/TAP adapters via Get-NetAdapter (read-only).
    Returns list of (Name, InterfaceDescription, Status, ifIndex) tuples."""
    found = []
    try:
        output = _run_powershell_hidden(
            'Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, ifIndex | '
            'ForEach-Object { \'{0}|{1}|{2}|{3}\' -f $_.Name, $_.InterfaceDescription, $_.Status, $_.ifIndex }',
            timeout=15,
        ).strip()
        for line in output.splitlines():
            if not line or "|" not in line:
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            name, desc, status, ifidx = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            combined = (name + " " + desc).lower()
            for kw in _VIRTUAL_KEYWORDS_NET:
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

    lines.append("--- Portal连通性诊断 ---")
    lines.append("Portal地址: {0}".format(host))
    lines.append("Portal基础URL: {0}".format(portal_base.rstrip("/")))
    lines.append("状态URL: {0}/drcom/chkstatus".format(portal_base.rstrip("/")))
    lines.append("登录URL: {0}://{1}:801/eportal/portal/login".format(scheme, host))

    # Boot time info
    boot_elapsed = _seconds_since_boot()
    if boot_elapsed > 0:
        lines.append("系统运行时间: {0}秒 ({1}分钟)".format(boot_elapsed, boot_elapsed // 60))
        if boot_elapsed < BOOT_GRACE_SECONDS:
            lines.append("警告: 仍在开机等待期({0}秒/{1}秒)".format(
                boot_elapsed, BOOT_GRACE_SECONDS))

    # Portal route info (the CORRECT interface for reaching the portal)
    route_info = _get_portal_route_info(host)
    lines.append("Portal路由接口: {0} (ifIndex={1})".format(
        route_info.get("alias") or "unknown", route_info.get("ifIndex") or "?"))
    lines.append("Portal路由源IP: {0}".format(route_info.get("sourceIP") or "未知"))
    lines.append("Portal路由下一跳: {0}".format(route_info.get("nextHop") or "未知"))
    lines.append("Portal路由度量: {0}".format(route_info.get("metric") or "未知"))

    # Default gateway
    gw = _get_default_gateway()
    lines.append("默认网关: {0}".format(gw or "未知"))

    # Warn if route source looks like virtual adapter
    src_ip = route_info.get("sourceIP") or ""
    if src_ip.startswith("192.168.144.") or src_ip.startswith("192.168.56.") or src_ip.startswith("172.16."):
        lines.append("警告: Portal路由源IP可能属于虚拟/仅主机适配器")

    # Virtual adapters
    vnet = _detect_virtual_adapters()
    if vnet:
        for name, desc, status, ifidx in vnet:
            lines.append("虚拟适配器: {0} ({1}) [{2}] ifIndex={3}".format(name, desc, status, ifidx))
    else:
        lines.append("虚拟适配器: 未检测到")

    # Socket test port 80
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        lines.append("Socket {0}:{1}: 成功".format(host, port))
    except OSError as exc:
        lines.append("Socket {0}:{1}: 失败 - {2}".format(host, port, exc))

    # Socket test port 801
    try:
        sock = socket.create_connection((host, 801), timeout=timeout)
        sock.close()
        lines.append("Socket {0}:801: 成功".format(host))
    except OSError as exc:
        lines.append("Socket {0}:801: 失败 - {1}".format(host, exc))

    # Raw direct HTTP status check
    try:
        text = fetch_direct_text(
            "{0}/drcom/chkstatus?callback=_diag&jsVersion=4.X&v=1&lang=zh".format(portal_base.rstrip("/")),
            headers={"User-Agent": "Mozilla/5.0 campus-auto-login-diag"},
            timeout=timeout,
        )
        obj = jsonp_to_obj(text)
        result_val = obj.get("result", "?")
        lines.append("直连HTTP状态: result={0} (成功)".format(result_val))
    except Exception as exc:
        lines.append("直连HTTP状态: 失败 - {0}".format(exc))

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
        lines.append("代理环境变量: {0}".format(", ".join(proxy_env_vals)))
    else:
        lines.append("代理环境变量: 无")

    # NO_PROXY check
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    has_portal_in_noproxy = "10." in no_proxy or "10.200.84.3" in no_proxy
    lines.append("NO_PROXY includes portal subnet: {0}".format("YES" if has_portal_in_noproxy else "NO"))

    lines.append("--- 诊断结束 ---")

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
    log_portal_failure_matrix(portal_base, log_fn=lambda msg: write_log(log_path, msg))


def log_portal_failure_matrix(portal_base, log_fn=None):
    """Log concrete route/source-IP evidence after resilient transport fails."""
    def _log(msg):
        if log_fn:
            log_fn(msg)

    host = parse.urlsplit(portal_base.rstrip("/")).hostname or "10.200.84.3"
    proxy_server, _proxy_override = _get_proxy_details()
    cached = _load_campus_route_cache()
    _log("=== 故障矩阵 ===")
    _log("SSID: {0}".format(get_current_wifi_ssid() or "未知"))
    _log("网关: {0}".format(_get_default_gateway() or "未知"))
    _log("代理: {0}".format(proxy_server or "未设置"))
    if cached:
        _log("缓存路由: gateway={0}, source_ip={1}, ifIndex={2}, ssid={3}, updated_at={4}".format(
            cached.get("gateway") or "?", cached.get("source_ip") or "?",
            cached.get("ifIndex") or "?", cached.get("ssid") or "?",
            cached.get("updated_at") or "?"))
        cached_ip = cached.get("source_ip")
        if cached_ip:
            try:
                s = socket.create_connection((host, 80), timeout=3, source_address=(cached_ip, 0))
                s.close()
                _log("  缓存源IP {0}: 成功".format(cached_ip))
            except OSError as e:
                _log("  缓存源IP {0}: 失败 - {1}".format(cached_ip, e))
    adapters = _get_physical_adapter_ips()
    for ip, ifidx, name, desc, virt in adapters:
        _log("  适配器: {0} IP={1} ifIdx={2} virtual={3}".format(name, ip, ifidx, virt))
    for ip, ifidx, name, desc, virt in adapters:
        try:
            s = socket.create_connection((host, 80), timeout=3, source_address=(ip, 0))
            s.close()
            _log("  源IP {0} ({1}): 成功".format(ip, name))
        except OSError as e:
            _log("  源IP {0} ({1}): 失败 - {2}".format(ip, name, e))
    _log("=== 故障矩阵结束 ===")


def wait_for_portal_ready(portal_base, timeout_seconds=60, interval=5, log_fn=None,
                           allow_proxy_bypass=False, campus_ssid=""):
    """Wait for the portal to become reachable. Returns status dict when ready, or None on timeout."""
    deadline = time.time() + timeout_seconds
    attempt = 0
    reconnect_requested = False
    while time.time() < deadline:
        attempt += 1
        status = get_status(portal_base, allow_proxy_bypass=allow_proxy_bypass)
        if status["state"] in ("online", "offline"):
            status["attempts"] = attempt
            _cache_campus_route(portal_base)
            elapsed = int(time.time() + timeout_seconds - deadline)
            if log_fn:
                log_fn("portal恢复可达({0}秒, {1}次尝试)".format(elapsed, attempt))
            return status
        if log_fn and attempt == 1:
            log_fn("等待portal恢复(最多{0}秒)...".format(timeout_seconds))
        if not reconnect_requested and status["state"] == "network_not_ready":
            reconnect_campus_wifi(campus_ssid, log_fn=log_fn)
            reconnect_requested = True
        remaining = deadline - time.time()
        sleep_time = min(interval, max(1, remaining))
        if sleep_time <= 0:
            break
        time.sleep(sleep_time)
    if log_fn:
        log_fn("portal恢复超时({0}秒)".format(timeout_seconds))
    return None


# ---------------------------------------------------------------------------
# Tray mode: system tray icon + tkinter log window
# ---------------------------------------------------------------------------

_CTRL_HANDLER = None  # prevent garbage collection


def _console_ctrl_handler(ctrl_type):
    """Ignore console close events so closing the console window doesn't kill the process.
    Allow system logoff/shutdown to proceed normally."""
    if ctrl_type == 2:  # CTRL_CLOSE_EVENT only
        return True
    return False


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
    lines_added = False
    while True:
        try:
            line = _log_queue.get_nowait()
            if not lines_added:
                text_widget.configure(state="normal")
                lines_added = True
            text_widget.insert("end", line + "\n")
            text_widget.see("end")
        except queue.Empty:
            break
    if lines_added:
        text_widget.configure(state="disabled")
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

    text_widget = ScrolledText(win, font=("Consolas", 9), wrap="word", state="disabled")
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
    _restore_system_sleep()
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

    # Prevent Windows from entering low-power sleep that disconnects Wi-Fi
    _prevent_system_sleep()
    atexit.register(_restore_system_sleep)

    # Disable Wi-Fi adapter power saving to stay connected during lock screen
    disable_wifi_power_save(log_fn=lambda msg: write_log(args.log, msg))

    tray_icon_img = create_tray_icon_image()
    icon = pystray.Icon("campus-auto-login", tray_icon_img, "校园网自动登录", _build_menu())

    def login_loop():
        MAX_LOOP_RESTARTS = 10  # prevent infinite restart storms
        restart_count = 0
        while restart_count < MAX_LOOP_RESTARTS:
            try:
                _run_login_loop_inner(args)
                # Normal return means config failure or early exit - treat as needing restart
                restart_count += 1
                write_log(args.log, "监控线程异常退出，{0}秒后重启（{1}/{2}）".format(
                    min(30, 5 * restart_count), restart_count, MAX_LOOP_RESTARTS))
                time.sleep(min(30, 5 * restart_count))
            except Exception as exc:
                restart_count += 1
                write_log(args.log, "监控线程崩溃（{0}），{1}秒后重启（{2}/{3}）".format(
                    exc, min(30, 5 * restart_count), restart_count, MAX_LOOP_RESTARTS))
                time.sleep(min(30, 5 * restart_count))
        write_log(args.log, "监控线程重启次数过多，停止监控")

    def _run_login_loop_inner(args):
        """Core login loop logic. Separated for crash recovery wrapper."""
        try:
            config = read_config(args.config)
        except Exception as exc:
            write_log(args.log, "配置读取失败:{0}: {1}".format(type(exc).__name__, exc))
            return
        if args.portal_base != DEFAULT_PORTAL:
            config["portal_base"] = args.portal_base.rstrip("/")
        write_log(args.log, "已启动，监控间隔={0}s".format(args.interval))
        # Boot grace period: wait for network to stabilize after system startup
        boot_grace_wait(log_fn=lambda msg: write_log(args.log, msg))
        # Network ready gate: wait for physical adapter, route, and TCP before probing
        portal_host = parse.urlsplit(config["portal_base"]).hostname or "10.200.84.3"
        if not wait_for_network_ready(portal_host, log_fn=lambda msg: write_log(args.log, msg)):
            write_log(args.log, "网络未就绪，进入正常重试")
        # Portal auto-discovery at startup
        discovered = discover_portal_base(
            config["portal_base"], timeout=3,
            log_fn=lambda msg: write_log(args.log, msg),
        )
        if discovered.rstrip("/") != config["portal_base"].rstrip("/"):
            write_log(args.log, "发现portal: {0}".format(discovered))
            config["portal_base"] = discovered
        failure_state = {"consecutive_failures": 0}
        FAST_INTERVAL = 10  # seconds between checks when network is down
        while True:
            try:
                login_once(config, args, failure_state=failure_state)
            except Exception as exc:
                write_log(args.log, "登录异常（{0}），{1}秒后重试".format(exc, args.interval))
            # Dynamic interval: fast when recovering, normal when stable
            if failure_state["consecutive_failures"] > 0:
                sleep_time = FAST_INTERVAL
            else:
                sleep_time = args.interval
            # Sleep with wake-from-sleep detection:
            # If wall-clock jumps more than interval*2, system just woke from sleep.
            # In that case, skip remaining sleep and check immediately.
            wall_start = time.time()
            time.sleep(sleep_time)
            wall_elapsed = time.time() - wall_start
            if wall_elapsed > sleep_time * 2:
                write_log(args.log, "唤醒检测，立即检查网络")

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
        init_result = init_config(args)
        if init_result is None and not _has_console_stdin():
            # GUI init was cancelled - abort
            return 0
        if not args.once and not args.check and not args.tray:
            return 0
    if args.check:
        return check_only(args)

    if args.check_wifi:
        ssid = get_current_wifi_ssid()
        write_log(args.log, "当前Wi-Fi: {0}".format(ssid or "未连接"))
        if args.campus_ssid:
            check_wifi_and_warn(args.campus_ssid, log_fn=lambda msg: write_log(args.log, msg))
        return 0

    if args.set_campus_ssid:
        ssid = get_current_wifi_ssid()
        if not ssid:
            write_log(args.log, "未检测到Wi-Fi，请先连接校园网Wi-Fi")
            return 1
        write_log(args.log, "当前Wi-Fi: {0}".format(ssid))
        write_log(args.log, "保存为校园网SSID:")
        write_log(args.log, '  campus_auto_login_cli.exe --init --campus-ssid "{0}"'.format(ssid))
        return 0

    if args.force_portal_reachable:
        write_log(args.log, "=== 强制portal连通模式 ===")
        write_log(args.log, "目标portal: {0}".format(args.portal_base))
        ssid = get_current_wifi_ssid()
        write_log(args.log, "当前Wi-Fi: {0}".format(ssid or "未连接"))
        gw = _get_default_gateway()
        write_log(args.log, "默认网关: {0}".format(gw or "未知"))
        proxy_server, proxy_override = _get_proxy_details()
        write_log(args.log, "系统代理: {0}".format(proxy_server or "未设置"))
        # Try each layer explicitly, but allow time for Wi-Fi/DHCP route recovery.
        test_url = "{0}/drcom/chkstatus?callback=_fr&jsVersion=4.X&v=1&lang=zh".format(args.portal_base.rstrip("/"))
        deadline = time.time() + 75
        last_error = None
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                content, layer = fetch_portal_text_resilient(
                    test_url, timeout=5, purpose="force",
                    allow_proxy_bypass=args.allow_temporary_proxy_bypass,
                )
                write_log(args.log, "portal可达: {0}".format(layer))
                _cache_campus_route(args.portal_base)
                return 0
            except OSError as exc:
                last_error = exc
                if attempt == 1:
                    write_log(args.log, "等待网络恢复(最多75秒)...")
                    reconnect_campus_wifi(args.campus_ssid, log_fn=lambda msg: write_log(args.log, msg))
                time.sleep(5)
        try:
            raise last_error or OSError("portal unreachable")
        except OSError as exc:
            write_log(args.log, "所有传输层失败(75秒): {0}".format(exc))
            log_portal_failure_matrix(args.portal_base, log_fn=lambda msg: write_log(args.log, msg))
            return 1

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
            write_log(args.log, "多层传输成功: {0}".format(layer))
            _cache_campus_route(args.portal_base)
        except OSError as exc:
            write_log(args.log, "多层传输失败: {0}".format(exc))
        # Portal auto-discovery
        discovered = discover_portal_base(
            args.portal_base, timeout=3,
            log_fn=lambda msg: write_log(args.log, msg),
        )
        write_log(args.log, "发现portal: {0}".format(discovered))
        return 0

    if args.once:
        config = read_config(args.config)
        if args.portal_base != DEFAULT_PORTAL:
            config["portal_base"] = args.portal_base.rstrip("/")
        campus_ssid = args.campus_ssid or config.get("campus_ssid", "")
        if campus_ssid:
            check_wifi_and_warn(campus_ssid, log_fn=lambda msg: write_log(args.log, msg))
        # Boot grace period
        boot_grace_wait(log_fn=lambda msg: write_log(args.log, msg))
        # Network ready gate
        portal_host = parse.urlsplit(config["portal_base"]).hostname or "10.200.84.3"
        if not wait_for_network_ready(portal_host, timeout_seconds=90, log_fn=lambda msg: write_log(args.log, msg)):
            write_log(args.log, "网络就绪等待超时，继续尝试")
        # Portal auto-discovery for --once mode
        discovered = discover_portal_base(
            config["portal_base"], timeout=3,
            log_fn=lambda msg: write_log(args.log, msg),
        )
        if discovered.rstrip("/") != config["portal_base"].rstrip("/"):
            write_log(args.log, "发现portal: {0}".format(discovered))
            config["portal_base"] = discovered
        # Wait for portal to become ready (--once can wait longer)
        status = wait_for_portal_ready(
            config["portal_base"], timeout_seconds=60, interval=5,
            log_fn=lambda msg: write_log(args.log, msg),
            allow_proxy_bypass=args.allow_temporary_proxy_bypass,
            campus_ssid=campus_ssid,
        )
        if status is None:
            write_log(args.log, "portal不可达，校园网可能未连接")
            diagnose_portal_connectivity(config["portal_base"], log_fn=lambda msg: write_log(args.log, msg))
            log_portal_failure_matrix(config["portal_base"], log_fn=lambda msg: write_log(args.log, msg))
            return 1
        if status["online"]:
            write_log(args.log, "已连接，无需登录")
            return 0
        # Portal reachable and offline - attempt login
        write_log(args.log, "portal可达，账号未认证，尝试登录...")
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

    # Disable Wi-Fi power saving for non-tray mode too
    disable_wifi_power_save(log_fn=lambda msg: write_log(args.log, msg))

    if requested_interval != args.interval:
        write_log(
            args.log,
            "间隔调整: {0}秒 -> {1}秒".format(requested_interval, args.interval),
        )
    write_log(args.log, "已启动，监控间隔={0}s".format(args.interval))
    failure_state = {"consecutive_failures": 0}
    FAST_INTERVAL = 10
    MAX_LOOP_RESTARTS = 10
    restart_count = 0
    while restart_count < MAX_LOOP_RESTARTS:
        try:
            while True:
                try:
                    login_once(config, args, failure_state=failure_state)
                except Exception as exc:
                    write_log(args.log, "登录异常（{0}），{1}秒后重试".format(exc, args.interval))
                if failure_state["consecutive_failures"] > 0:
                    sleep_time = FAST_INTERVAL
                else:
                    sleep_time = args.interval
                wall_start = time.time()
                time.sleep(sleep_time)
                wall_elapsed = time.time() - wall_start
                if wall_elapsed > sleep_time * 2:
                    write_log(args.log, "唤醒检测，立即检查网络")
        except Exception as exc:
            restart_count += 1
            write_log(args.log, "监控循环崩溃（{0}），{1}秒后重启（{2}/{3}）".format(
                exc, min(30, 5 * restart_count), restart_count, MAX_LOOP_RESTARTS))
            time.sleep(min(30, 5 * restart_count))
    write_log(args.log, "监控循环重启次数过多，停止")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        write_log(DEFAULT_LOG, "用户停止")
        raise SystemExit(130)
    except Exception as exc:
        write_log(DEFAULT_LOG, "致命错误: {0}: {1}".format(type(exc).__name__, exc))
        if getattr(sys, "frozen", False):
            time.sleep(15)
        raise SystemExit(1)
