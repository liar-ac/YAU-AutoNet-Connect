#!/usr/bin/env python3
import argparse
import base64
import ctypes
import getpass
import json
import queue
import random
import re
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
APP_VERSION = "1.0.1"
__version__ = APP_VERSION


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
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-python",
            "Accept": "*/*",
        },
    )
    with request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8", errors="replace")
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
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Windows NT 10.0 Win64 x64 campus-auto-login-python",
            "Accept": "*/*",
            "Referer": referer,
        },
    )
    with request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8", errors="replace")
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
    try:
        data = invoke_jsonp(portal_base, "/drcom/chkstatus", timeout=8)
        return {
            "reachable": True,
            "online": int(data.get("result", 0)) == 1,
            "raw": data,
            "error": None,
        }
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        return {
            "reachable": False,
            "online": False,
            "raw": None,
            "error": str(exc),
        }


def login_once(config, args):
    status = get_status(config["portal_base"])
    if not status["reachable"]:
        write_log(args.log, "Portal unreachable:{0}".format(status["error"]))
        return False
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
    return parser.parse_args()


def normalize_interval(seconds):
    if seconds < MIN_INTERVAL_SECONDS:
        return MIN_INTERVAL_SECONDS
    if seconds > MAX_INTERVAL_SECONDS:
        return MAX_INTERVAL_SECONDS
    return seconds


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
        while True:
            try:
                login_once(config, args)
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

    if args.once:
        config = read_config(args.config)
        if args.portal_base != DEFAULT_PORTAL:
            config["portal_base"] = args.portal_base.rstrip("/")
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
