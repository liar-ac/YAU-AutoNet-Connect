#!/usr/bin/env python3
"""
Integration tests for campus_auto_login.py

Tests end-to-end workflows, network failure scenarios, and config migration.
These tests use the REAL field-name contracts of read_config/login_once and
isolate global state + side effects so they never touch the real %APPDATA%.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import campus_auto_login


class _GlobalStateIsolation(unittest.TestCase):
    """Base class that snapshots and restores module-level mutable globals so
    one test can never leak login-param/discovery state into another."""

    def setUp(self):
        self._saved_cached_login = campus_auto_login._cached_login_params
        self._saved_last_discovery = campus_auto_login._last_discovery_time
        campus_auto_login._cached_login_params = None
        campus_auto_login._last_discovery_time = 0

    def tearDown(self):
        campus_auto_login._cached_login_params = self._saved_cached_login
        campus_auto_login._last_discovery_time = self._saved_last_discovery


class TestConfigMigration(_GlobalStateIsolation):
    """Test reading config files written by older versions / PowerShell."""

    def setUp(self):
        super().setUp()
        self.temp_dir = Path(tempfile.mkdtemp())
        # Avoid copying the temp config into the real %APPDATA%.
        self._migrate_patch = patch.object(campus_auto_login, "_maybe_migrate_config", lambda *a, **k: None)
        self._migrate_patch.start()

    def tearDown(self):
        self._migrate_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        super().tearDown()

    def test_migrate_from_v1_0_config(self):
        """Old Python config (no _checksum) reads via the no_checksum path."""
        config_path = self.temp_dir / "campus_login_py.config.json"
        old_config = {
            "username": "testuser",
            "password_dpapi": "AQAAANCMnd8BFdERjHoAwE_dummy_base64",
            "portal_base": "http://10.200.84.3",
            "service_suffix": "@cmcc",
        }
        config_path.write_text(json.dumps(old_config), encoding="utf-8")

        with patch("campus_auto_login.dpapi_unprotect", return_value="password123"):
            config = campus_auto_login.read_config(config_path)

        self.assertEqual(config["username"], "testuser")
        self.assertEqual(config["portal_base"], "http://10.200.84.3")
        self.assertEqual(config["config_format"], "python")

    def test_migrate_from_powershell_config(self):
        """PowerShell config (Username/Password fields) maps onto python schema."""
        ps_config_path = self.temp_dir / "campus_login.config.json"
        ps_config = {
            "Username": "testuser",
            "Password": "01000000d08c9ddf0115d1118c7a00c04fc297eb",
            "PortalBase": "http://10.200.84.3",
            "ServiceSuffix": "@cmcc",
            "TerminalType": 1,
        }
        ps_config_path.write_text(json.dumps(ps_config), encoding="utf-8")

        with patch("campus_auto_login.dpapi_unprotect_powershell_secure_string", return_value="password123"):
            config = campus_auto_login.read_config(ps_config_path)

        self.assertEqual(config["username"], "testuser")
        self.assertEqual(config["password_ps_hex"], ps_config["Password"])
        self.assertEqual(config["config_format"], "powershell")

    def test_corrupted_config_is_rejected_and_backed_up(self):
        """A config whose stored _checksum does not match must be rejected and
        a .corrupted backup left behind (config-protection contract from #15)."""
        config_path = self.temp_dir / "campus_login_py.config.json"
        tampered = {
            "username": "testuser",
            "password_dpapi": "dummy",
            "portal_base": "http://10.200.84.3",
            "_checksum": "0000000000000000000000000000000000000000000000000000000000000000",
        }
        config_path.write_text(json.dumps(tampered), encoding="utf-8")

        with self.assertRaises(ValueError):
            campus_auto_login.read_config(config_path)

        backups = list(self.temp_dir.glob("*.corrupted.*"))
        self.assertTrue(backups, "tampered config should be backed up")


class TestNetworkFailureScenarios(_GlobalStateIsolation):
    """Simulate the network states get_status can return and assert recovery."""

    def setUp(self):
        super().setUp()
        self.config = {
            "username": "testuser",
            "password_dpapi": "encrypted",
            "portal_base": "http://10.200.84.3",
            "service_suffix": "",
            "terminal_type": 1,
        }
        self.args = MagicMock()
        self.args.log = Path("test.log")
        self.args.allow_temporary_proxy_bypass = False
        self.args.campus_ssid = ""
        self.args.check = False
        self.args.max_attempts = 1

    def test_socket_timeout_triggers_wifi_reconnect(self):
        """network_not_ready (socket error) must trigger a Wi-Fi reconnect."""
        with patch("campus_auto_login.get_status") as mock_status, \
             patch("campus_auto_login.reconnect_campus_wifi") as mock_reconnect, \
             patch("campus_auto_login.time.sleep"), \
             patch("campus_auto_login.write_log"):
            # First probe: socket down. Retry after reconnect: still down -> return False.
            mock_status.return_value = {
                "state": "network_not_ready", "online": False, "reachable": False,
            }
            result = campus_auto_login.login_once(self.config, self.args, failure_state={"consecutive_failures": 0})

            mock_reconnect.assert_called_once()
            self.assertFalse(result)

    def test_portal_unreachable_triggers_discovery(self):
        """portal_unreachable (with cooldown elapsed) must invoke discovery."""
        failure_state = {"consecutive_failures": 0}
        with patch("campus_auto_login.get_status") as mock_status, \
             patch("campus_auto_login.discover_portal_base") as mock_discover, \
             patch("campus_auto_login.time.time", return_value=100000), \
             patch("campus_auto_login.write_log"):
            mock_status.return_value = {
                "state": "portal_unreachable", "online": False, "reachable": False,
            }
            # Discovery returns the SAME base -> login_once returns False without
            # needing a second get_status, keeping the test focused on discovery.
            mock_discover.return_value = self.config["portal_base"]

            result = campus_auto_login.login_once(self.config, self.args, failure_state=failure_state)

            mock_discover.assert_called_once()
            self.assertFalse(result)

    def test_consecutive_failures_increment_on_unreachable(self):
        """The failure counter feeds the dynamic fast-retry interval; verify it grows."""
        failure_state = {"consecutive_failures": 0}
        with patch("campus_auto_login.get_status") as mock_status, \
             patch("campus_auto_login.discover_portal_base", return_value=self.config["portal_base"]), \
             patch("campus_auto_login.time.time", return_value=100000), \
             patch("campus_auto_login.write_log"):
            mock_status.return_value = {
                "state": "portal_unreachable", "online": False, "reachable": False,
            }
            campus_auto_login.login_once(self.config, self.args, failure_state=failure_state)

            self.assertEqual(failure_state["consecutive_failures"], 1)


class TestEndToEnd(_GlobalStateIsolation):
    """End-to-end-ish workflow checks that don't require a live portal."""

    def test_first_run_detection_when_no_config(self):
        """_find_config_file returns None when nothing exists -> first-run path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "campus_login_py.config.json"
            self.assertIsNone(campus_auto_login._find_config_file(missing))

    def test_saved_config_round_trips(self):
        """A config written with username/password_dpapi reads back intact."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "campus_login_py.config.json"
            data = {
                "username": "testuser",
                "password_dpapi": "AQAAANCMnd8_dummy",
                "portal_base": "http://10.200.84.3",
                "service_suffix": "@cmcc",
                "terminal_type": 1,
            }
            config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            with patch.object(campus_auto_login, "_maybe_migrate_config", lambda *a, **k: None), \
                 patch("campus_auto_login.dpapi_unprotect", return_value="password123"):
                config = campus_auto_login.read_config(config_path)

            self.assertEqual(config["username"], "testuser")
            self.assertEqual(config["service_suffix"], "@cmcc")


class TestProxyHandling(unittest.TestCase):
    """Clash/system-proxy handling: raw direct transport must bypass proxy."""

    def test_raw_direct_connection(self):
        """fetch_direct_text builds an http.client connection (bypassing urllib proxy)."""
        with patch("campus_auto_login.http.client.HTTPConnection") as mock_conn:
            mock_instance = MagicMock()
            mock_conn.return_value = mock_instance
            resp = mock_instance.getresponse.return_value
            resp.read.return_value = b"test"
            resp.status = 200

            result = campus_auto_login.fetch_direct_text("http://10.200.84.3/test", timeout=5)

            mock_conn.assert_called_once()
            self.assertEqual(result, "test")

    def test_https_url_rejected_by_raw_direct(self):
        """fetch_direct_text only supports http:// (portal is plain http)."""
        with self.assertRaises(ValueError):
            campus_auto_login.fetch_direct_text("https://10.200.84.3/test", timeout=5)


class TestI18n(unittest.TestCase):
    """Test the lightweight i18n framework."""

    def setUp(self):
        self._saved_lang = campus_auto_login._i18n_lang
        campus_auto_login._i18n_lang = "zh"

    def tearDown(self):
        campus_auto_login._i18n_lang = self._saved_lang

    def test_default_language_is_zh(self):
        """Default language should be Chinese."""
        self.assertEqual(campus_auto_login._i18n_lang, "zh")
        self.assertIn("zh", campus_auto_login._i18n_strings["login_success"])

    def test_t_returns_zh_by_default(self):
        """t() returns Chinese when language is zh."""
        result = campus_auto_login.t("login_success")
        self.assertIn("登录成功", result)

    def test_t_returns_en_when_switched(self):
        """t() returns English when language is switched to en."""
        campus_auto_login._i18n_lang = "en"
        result = campus_auto_login.t("login_success")
        self.assertIn("Login successful", result)

    def test_t_with_format_args(self):
        """t() supports positional format args."""
        result = campus_auto_login.t("started_monitoring", 30)
        self.assertIn("30", result)

    def test_t_unknown_key_returns_key(self):
        """t() gracefully returns the key itself for unknown entries."""
        result = campus_auto_login.t("nonexistent_key_xyz")
        self.assertEqual(result, "nonexistent_key_xyz")

    def test_t_format_fallback_on_bad_args(self):
        """t() returns the message even if format args are wrong."""
        result = campus_auto_login.t("login_success", "extra", "args")
        self.assertIn("登录成功", result)

    def test_catalog_has_min_entries(self):
        """The translation catalog should have a reasonable number of entries."""
        self.assertGreaterEqual(len(campus_auto_login._i18n_strings), 80)

    def test_all_entries_have_zh(self):
        """Every catalog entry must have a 'zh' translation."""
        for key, entry in campus_auto_login._i18n_strings.items():
            self.assertIn("zh", entry, f"Missing 'zh' for key '{key}'")

    def test_all_entries_have_en(self):
        """Every catalog entry must have an 'en' translation."""
        for key, entry in campus_auto_login._i18n_strings.items():
            self.assertIn("en", entry, f"Missing 'en' for key '{key}'")

    def test_language_switch_roundtrip(self):
        """Switching language and back yields correct results."""
        campus_auto_login._i18n_lang = "en"
        en_result = campus_auto_login.t("login_success")
        campus_auto_login._i18n_lang = "zh"
        zh_result = campus_auto_login.t("login_success")
        self.assertNotEqual(en_result, zh_result)
        self.assertIn("Login", en_result)
        self.assertIn("登录", zh_result)


if __name__ == "__main__":
    unittest.main()
