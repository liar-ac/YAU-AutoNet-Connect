#!/usr/bin/env python3
"""Lightweight tests for campus_auto_login (no network required)."""
import json
import unittest
from unittest.mock import MagicMock, patch

from campus_auto_login import (
    DIRECT_OPENER,
    account_prefix,
    client_info_from_status,
    eportal_login_url,
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
    """Verify that portal requests use a direct opener to bypass system proxy."""

    def test_direct_opener_exists(self):
        self.assertIsNotNone(DIRECT_OPENER)

    def test_open_direct_exists(self):
        self.assertTrue(callable(open_direct))

    def test_direct_opener_has_handlers(self):
        self.assertIsNotNone(DIRECT_OPENER.handlers)
        self.assertTrue(len(DIRECT_OPENER.handlers) > 0)

    @patch("campus_auto_login.open_direct")
    def test_invoke_jsonp_uses_open_direct(self, mock_open):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'callback({"result":1});'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = invoke_jsonp("http://10.200.84.3", "/drcom/chkstatus")
        self.assertEqual(result["result"], 1)
        mock_open.assert_called_once()
        args = mock_open.call_args
        self.assertIn("10.200.84.3", args[0][0].full_url)

    @patch("campus_auto_login.open_direct")
    def test_invoke_url_jsonp_uses_open_direct(self, mock_open):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'callback({"result":"ok"});'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        result = invoke_url_jsonp(
            "http://10.200.84.3:801/eportal/portal/login",
            [("user_account", "test")],
            portal_base="http://10.200.84.3",
        )
        self.assertEqual(result["result"], "ok")
        mock_open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
