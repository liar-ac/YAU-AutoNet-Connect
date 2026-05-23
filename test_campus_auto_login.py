#!/usr/bin/env python3
"""Lightweight tests for campus_auto_login (no network required)."""
import json
import unittest
from unittest.mock import MagicMock, patch

from campus_auto_login import (
    DIRECT_OPENER,
    account_prefix,
    client_info_from_status,
    diagnose_portal_connectivity,
    eportal_login_url,
    fetch_direct_text,
    invoke_jsonp,
    invoke_url_jsonp,
    jsonp_to_obj,
    normalize_interval,
    open_direct,
    query_string,
    __version__,
)


class TestVersion(unittest.TestCase):
    def test_version_string(self):
        self.assertIsInstance(__version__, str)
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


class TestJsonpToObj(unittest.TestCase):
    def test_jsonp(self):
        result = jsonp_to_obj('callback({"result":1});')
        self.assertEqual(result["result"], 1)

    def test_plain_json(self):
        result = jsonp_to_obj('{"result":0}')
        self.assertEqual(result["result"], 0)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            jsonp_to_obj("not json at all")


class TestQueryString(unittest.TestCase):
    def test_basic(self):
        qs = query_string([("a", "1"), ("b", "hello")])
        self.assertIn("a=1", qs)
        self.assertIn("b=hello", qs)
        self.assertIn("&", qs)


class TestAccountPrefix(unittest.TestCase):
    def test_pc(self):
        self.assertEqual(account_prefix(1), ",0,")

    def test_mobile(self):
        self.assertEqual(account_prefix(2), ",1,")


class TestNormalizeInterval(unittest.TestCase):
    def test_too_low(self):
        self.assertEqual(normalize_interval(1), 5)

    def test_too_high(self):
        self.assertEqual(normalize_interval(100), 30)

    def test_normal(self):
        self.assertEqual(normalize_interval(15), 15)


class TestEportalLoginUrl(unittest.TestCase):
    def test_default(self):
        url = eportal_login_url("http://10.200.84.3")
        self.assertEqual(url, "http://10.200.84.3:801/eportal/portal/login")

    def test_custom(self):
        url = eportal_login_url("http://10.200.100.1")
        self.assertEqual(url, "http://10.200.100.1:801/eportal/portal/login")


class TestClientInfoFromStatus(unittest.TestCase):
    def test_normal(self):
        raw = {"v4ip": "10.20.30.40", "ss4": "aa-bb-cc-dd-ee-ff"}
        ip, mac = client_info_from_status(raw)
        self.assertEqual(ip, "10.20.30.40")
        self.assertEqual(mac, "aabbccddeeff")

    def test_empty(self):
        ip, mac = client_info_from_status({})
        self.assertEqual(ip, "")
        self.assertEqual(mac, "000000000000")

    def test_none(self):
        ip, mac = client_info_from_status(None)
        self.assertEqual(ip, "")
        self.assertEqual(mac, "000000000000")


class TestDirectOpener(unittest.TestCase):
    """Verify that legacy DIRECT_OPENER still exists for backward compatibility."""

    def test_direct_opener_exists(self):
        self.assertIsNotNone(DIRECT_OPENER)

    def test_open_direct_exists(self):
        self.assertTrue(callable(open_direct))


class TestFetchDirectText(unittest.TestCase):
    """Verify fetch_direct_text uses http.client.HTTPConnection."""

    @patch("campus_auto_login.http.client.HTTPConnection")
    def test_uses_http_client(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'callback({"result":1});'
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp
        mock_conn_cls.return_value = mock_conn

        result = fetch_direct_text(
            "http://10.200.84.3/drcom/chkstatus?callback=test",
            headers={"User-Agent": "test"},
            timeout=5,
        )
        self.assertEqual(result, 'callback({"result":1});')
        mock_conn_cls.assert_called_once_with("10.200.84.3", 80, timeout=5)
        mock_conn.request.assert_called_once()
        call_args = mock_conn.request.call_args
        self.assertEqual(call_args[0][0], "GET")
        self.assertIn("/drcom/chkstatus", call_args[0][1])
        mock_conn.close.assert_called_once()

    @patch("campus_auto_login.http.client.HTTPConnection")
    def test_path_and_query_preserved(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'ok'
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp
        mock_conn_cls.return_value = mock_conn

        fetch_direct_text("http://10.200.84.3:801/eportal/portal/login?a=1&b=2")
        call_args = mock_conn.request.call_args
        path_used = call_args[0][1]
        self.assertIn("/eportal/portal/login", path_used)
        self.assertIn("a=1", path_used)
        self.assertIn("b=2", path_used)

    @patch("campus_auto_login.http.client.HTTPConnection")
    def test_custom_port(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'ok'
        mock_resp.status = 200
        mock_conn.getresponse.return_value = mock_resp
        mock_conn_cls.return_value = mock_conn

        fetch_direct_text("http://10.200.84.3:801/test")
        mock_conn_cls.assert_called_once_with("10.200.84.3", 801, timeout=10)

    @patch("campus_auto_login.http.client.HTTPConnection")
    def test_http_error_raises(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'error'
        mock_resp.status = 404
        mock_resp.reason = "Not Found"
        mock_conn.getresponse.return_value = mock_resp
        mock_conn_cls.return_value = mock_conn

        with self.assertRaises(OSError) as ctx:
            fetch_direct_text("http://10.200.84.3/notfound")
        self.assertIn("404", str(ctx.exception))


class TestInvokeJsonpUsesFetchDirect(unittest.TestCase):
    @patch("campus_auto_login.fetch_direct_text")
    def test_invoke_jsonp_calls_fetch_direct(self, mock_fetch):
        mock_fetch.return_value = 'callback({"result":1});'
        result = invoke_jsonp("http://10.200.84.3", "/drcom/chkstatus")
        self.assertEqual(result["result"], 1)
        mock_fetch.assert_called_once()
        url = mock_fetch.call_args[0][0]
        self.assertIn("10.200.84.3", url)
        self.assertIn("/drcom/chkstatus", url)

    @patch("campus_auto_login.fetch_direct_text")
    def test_invoke_url_jsonp_calls_fetch_direct(self, mock_fetch):
        mock_fetch.return_value = 'callback({"result":"ok"});'
        result = invoke_url_jsonp(
            "http://10.200.84.3:801/eportal/portal/login",
            [("user_account", "test")],
            portal_base="http://10.200.84.3",
        )
        self.assertEqual(result["result"], "ok")
        mock_fetch.assert_called_once()

    @patch("campus_auto_login.fetch_direct_text")
    def test_invoke_url_jsonp_referer_uses_portal_base(self, mock_fetch):
        mock_fetch.return_value = 'callback({"result":"ok"});'
        invoke_url_jsonp(
            "http://10.200.84.3:801/eportal/portal/login",
            [],
            portal_base="http://10.200.100.1",
        )
        headers = mock_fetch.call_args[1]["headers"]
        self.assertEqual(headers["Referer"], "http://10.200.100.1/")


class TestDiagnosePortalConnectivity(unittest.TestCase):
    def test_returns_lines(self):
        with patch("campus_auto_login.socket.create_connection") as mock_sock, \
             patch("campus_auto_login._check_system_proxy_enabled", return_value=False), \
             patch("campus_auto_login._check_proxy_env", return_value=False):
            mock_sock.return_value = MagicMock()
            lines = diagnose_portal_connectivity("http://10.200.84.3")
            self.assertTrue(len(lines) > 5)
            self.assertTrue(any("Portal host" in l for l in lines))
            self.assertTrue(any("Socket" in l for l in lines))

    def test_socket_fail_shown(self):
        with patch("campus_auto_login.socket.create_connection", side_effect=OSError("test error")), \
             patch("campus_auto_login._check_system_proxy_enabled", return_value=False), \
             patch("campus_auto_login._check_proxy_env", return_value=False):
            lines = diagnose_portal_connectivity("http://10.200.84.3")
            self.assertTrue(any("FAIL" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
