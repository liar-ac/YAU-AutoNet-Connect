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


def invoke_jsonp(portal_base, path, params=None, timeout=10, force_trailing_lang=False):
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
    content = fetch_direct_text(url, headers=headers, timeout=timeout)
    return jsonp_to_obj(content)


def invoke_url_jsonp(url_base, params=None, timeout=10, force_trailing_lang=False, portal_base=None):
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
    content = fetch_direct_text(url, headers=headers, timeout=timeout)
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


def get_status(portal_base):
    """Check portal status. Returns a dict with 'state' field:
    - "online": portal reachable and user is logged in
    - "offline": portal reachable but user not logged in
    - "portal_unreachable": portal HTTP error (not socket-level)
    - "network_not_ready": socket-level failure (transient, may recover)
    """
    try:
        url = "{0}/drcom/chkstatus?callback=_ck&jsVersion=4.X&v={1}&lang=zh".format(
            portal_base.rstrip("/"), random.randint(500, 10500)
        )
        headers = {
            "User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-python",
            "Accept": "*/*",
        }
        content, attempts = fetch_text_with_retry(url, headers=headers, timeout=8)
        data = jsonp_to_obj(content)
        result_val = int(data.get("result", 0))
        return {
            "state": "online" if result_val == 1 else "offline",
            "reachable": True,
            "online": result_val == 1,
            "raw": data,
            "error": None,
            "attempts": attempts,
        }
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        is_socket = _is_socket_unreachable_error(exc)
        return {
            "state": "network_not_ready" if is_socket else "portal_unreachable",
            "reachable": False,
            "online": False,
            "raw": None,
            "error": str(exc),
            "is_network_unreachable": is_socket,
        }


def login_once(config, args, failure_state=None):
    """Attempt one login cycle. Returns True if online after this call.
    failure_state: dict to track consecutive failures across calls (for tray loop).
    """
    status = get_status(config["portal_base"])

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
            )
            result_value = result.get("result")
            if result_value == 1 or str(result_value).lower() in {"1", "ok"}:
                write_log(args.log, "Login API returned success.")
                time.sleep(2)
                after = get_status(config["portal_base"])
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
    return parser.parse_args()


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
    Tries: configured value -> NCSI probes -> gateway subnet candidates.
    Returns the working portal base URL, or the configured one if nothing found.
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    # 1. Try the configured portal
    configured = configured_portal_base.rstrip("/")
    if _test_portal_candidate(configured, timeout=timeout):
        _log("Portal confirmed: {0}".format(configured))
        return configured

    _log("Configured portal {0} not reachable, attempting auto-discovery...".format(configured))

    # 2. Try NCSI probe URLs to detect captive portal redirect
    for probe_url in _NCSI_PROBE_URLS:
        try:
            status, reason, headers, body = fetch_direct_raw(
                probe_url,
                headers={"User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-discovery"},
                timeout=timeout,
            )
            location = headers.get("Location") or headers.get("location", "")
            if location:
                candidate = _extract_portal_from_url(location)
                if candidate and _test_portal_candidate(candidate, timeout=timeout):
                    _log("Auto-discovered portal via redirect from {0}: {1}".format(probe_url, candidate))
                    return candidate
        except Exception:
            continue

    # 3. Try default gateway and nearby IPs
    gateway = _get_default_gateway()
    if gateway:
        _log("Default gateway: {0}, probing subnet...".format(gateway))
        for candidate in _get_gateway_subnet_candidates(gateway):
            if candidate.rstrip("/") == configured:
                continue  # already tried
            if _test_portal_candidate(candidate, timeout=timeout):
                _log("Auto-discovered portal via gateway subnet: {0}".format(candidate))
                return candidate

    _log("Auto-discovery failed, using configured portal: {0}".format(configured))
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


def _get_active_ipv4():
    """Get the active IPv4 address from the WLAN adapter (best-effort)."""
    try:
        output = os.popen("ipconfig").read(4096)
        # Look for WLAN section and its IPv4 address
        in_wlan = False
        for line in output.splitlines():
            if "WLAN" in line or "Wi-Fi" in line or "Wireless" in line:
                in_wlan = True
            elif in_wlan and ("IPv4" in line or "IP Address" in line):
                parts = line.split(":")
                if len(parts) >= 2:
                    addr = parts[1].strip().rstrip(".")
                    if addr and not addr.startswith("169.254"):
                        return addr
            elif in_wlan and line.strip() == "":
                in_wlan = False
        # Fallback: get any non-loopback IPv4
        for line in output.splitlines():
            if ("IPv4" in line or "IP Address" in line) and "127.0.0.1" not in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    addr = parts[1].strip().rstrip(".")
                    if addr and not addr.startswith("169.254"):
                        return addr
    except Exception:
        pass
    return "unknown"


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
    """Detect TUN/TAP/virtual network adapters (read-only)."""
    keywords = ["tun", "tap", "wintun", "clash", "meta", "wireguard", "vpn", "sstap", "secitap", "netease uu"]
    found = []
    try:
        output = os.popen("ipconfig /all").read(16384)
        current_adapter = ""
        for line in output.splitlines():
            if line and not line.startswith(" ") and "adapter" in line.lower():
                current_adapter = line.strip()
            elif current_adapter:
                lower = current_adapter.lower()
                for kw in keywords:
                    if kw in lower and current_adapter not in found:
                        found.append(current_adapter)
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

    # Active IPv4
    active_ip = _get_active_ipv4()
    lines.append("Active IPv4: {0}".format(active_ip))

    # Default gateway
    gw = _get_default_gateway()
    lines.append("Default gateway: {0}".format(gw or "unknown"))

    # Best route to portal
    try:
        route_output = os.popen("route print {0}".format(host)).read(1024)
        route_lines = [l for l in route_output.splitlines() if host in l or "0.0.0.0" in l]
        if route_lines:
            lines.append("Route entries: {0}".format("; ".join(route_lines[:3])))
        else:
            lines.append("Route entries: none specific (using default)")
    except Exception:
        lines.append("Route entries: unavailable")

    # Virtual adapters
    vnet = _detect_virtual_adapters()
    if vnet:
        lines.append("Virtual/TUN adapters detected: {0}".format(", ".join(vnet)))
    else:
        lines.append("Virtual/TUN adapters detected: none")

    # Socket test port 80
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        lines.append("Socket {0}:{1}: SUCCESS".format(host, port))
    except OSError as exc:
        lines.append("Socket {0}:{1}: FAIL - {2}".format(host, port, exc))

    # Socket test port 801
    try:
        sock = socket.create_connection((host, 801), timeout=timeout)
        sock.close()
        lines.append("Socket {0}:801: SUCCESS".format(host))
    except OSError as exc:
        lines.append("Socket {0}:801: FAIL - {1}".format(host, exc))

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
            # Show only host:port, not full URL with credentials
            try:
                p = parse.urlsplit(val)
                proxy_env_vals.append("{0}={1}:{2}".format(name, p.hostname, p.port))
            except Exception:
                proxy_env_vals.append("{0}=<set>".format(name))
    if proxy_env_vals:
        lines.append("Proxy env vars: {0}".format(", ".join(proxy_env_vals)))
    else:
        lines.append("Proxy env vars: none")

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


def wait_for_portal_ready(portal_base, timeout_seconds=60, interval=5, log_fn=None):
    """Wait for the portal to become reachable. Returns status dict when ready, or None on timeout."""
    deadline = time.time() + timeout_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        status = get_status(portal_base)
        if status["state"] in ("online", "offline"):
            if log_fn:
                log_fn("Portal reachable after {0}s ({1} attempts).".format(
                    int(attempt * interval), attempt))
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

    if args.init:
        init_config(args)
        if not args.once and not args.check and not args.tray:
            return 0
    if args.check:
        return check_only(args)

    if args.diagnose:
        lines = diagnose_portal_connectivity(args.portal_base)
        for line in lines:
            write_log(args.log, line)
        # Also run portal discovery
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
        )
        if status is None:
            write_log(args.log, "Portal not reachable after 60s. Campus network may not be connected.")
            diagnose_portal_connectivity(config["portal_base"], log_fn=lambda msg: write_log(args.log, msg))
            return 1
        if status["online"]:
            write_log(args.log, "Already online. No login needed.")
            return 0
        # Portal reachable and offline - attempt login
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
