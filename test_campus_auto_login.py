#!/usr/bin/env python3
"""Lightweight tests for campus_auto_login (no network required)."""
import json
import unittest
from unittest.mock import MagicMock, patch, call

from campus_auto_login import (
    DIRECT_OPENER,
    account_prefix,
    client_info_from_status,
    diagnose_portal_connectivity,
    discover_portal_base,
    eportal_login_url,
    fetch_direct_text,
    fetch_direct_raw,
    invoke_jsonp,
    invoke_url_jsonp,
    jsonp_to_obj,
    normalize_interval,
    open_direct,
    query_string,
    __version__,
    _extract_portal_from_url,
    _test_portal_candidate,
    _get_default_gateway,
    _get_gateway_subnet_candidates,
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
    def test_direct_opener_exists(self):
        self.assertIsNotNone(DIRECT_OPENER)

    def test_open_direct_exists(self):
        self.assertTrue(callable(open_direct))


class TestFetchDirectText(unittest.TestCase):
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


class TestFetchDirectRaw(unittest.TestCase):
    @patch("campus_auto_login.http.client.HTTPConnection")
    def test_returns_status_headers_body(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'hello'
        mock_resp.status = 302
        mock_resp.reason = "Found"
        mock_resp.getheaders.return_value = [("Location", "http://10.200.84.3/portal")]
        mock_conn.getresponse.return_value = mock_resp
        mock_conn_cls.return_value = mock_conn

        status, reason, headers, body = fetch_direct_raw("http://example.com/test")
        self.assertEqual(status, 302)
        self.assertEqual(reason, "Found")
        self.assertEqual(headers["Location"], "http://10.200.84.3/portal")
        self.assertEqual(body, "hello")


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


class TestExtractPortalFromUrl(unittest.TestCase):
    def test_extracts_portal_ip(self):
        result = _extract_portal_from_url("http://10.200.84.3/portal/login")
        self.assertEqual(result, "http://10.200.84.3")

    def test_extracts_portal_with_port(self):
        result = _extract_portal_from_url("http://10.200.84.3:8080/portal")
        self.assertEqual(result, "http://10.200.84.3:8080")

    def test_skips_known_public_hosts(self):
        self.assertIsNone(_extract_portal_from_url("http://www.msftconnecttest.com/connecttest.txt"))
        self.assertIsNone(_extract_portal_from_url("http://connectivitycheck.gstatic.com/generate_204"))
        self.assertIsNone(_extract_portal_from_url("http://neverssl.com/"))

    def test_returns_none_for_no_host(self):
        self.assertIsNone(_extract_portal_from_url(""))


class TestTestPortalCandidate(unittest.TestCase):
    @patch("campus_auto_login.fetch_direct_text")
    def test_returns_true_for_valid_portal(self, mock_fetch):
        mock_fetch.return_value = 'test_cb({"result":1})'
        self.assertTrue(_test_portal_candidate("http://10.200.84.3"))
        mock_fetch.assert_called_once()

    @patch("campus_auto_login.fetch_direct_text")
    def test_returns_false_on_error(self, mock_fetch):
        mock_fetch.side_effect = OSError("unreachable")
        self.assertFalse(_test_portal_candidate("http://10.200.84.3"))

    @patch("campus_auto_login.fetch_direct_text")
    def test_returns_false_for_non_portal_response(self, mock_fetch):
        mock_fetch.return_value = "<html>Not a portal</html>"
        self.assertFalse(_test_portal_candidate("http://10.200.84.3"))


class TestGetDefaultGateway(unittest.TestCase):
    @patch("campus_auto_login.os.popen")
    def test_parses_gateway(self, mock_popen):
        mock_popen.return_value.read.return_value = """
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      10.211.223.1   10.211.223.248     35
"""
        gw = _get_default_gateway()
        self.assertEqual(gw, "10.211.223.1")

    @patch("campus_auto_login.os.popen")
    def test_returns_none_on_failure(self, mock_popen):
        mock_popen.side_effect = Exception("fail")
        self.assertIsNone(_get_default_gateway())


class TestGetGatewaySubnetCandidates(unittest.TestCase):
    def test_generates_candidates(self):
        candidates = _get_gateway_subnet_candidates("10.211.223.1", count=5)
        self.assertTrue(len(candidates) > 0)
        self.assertIn("http://10.211.223.1", candidates)
        self.assertIn("http://10.211.223.3", candidates)

    def test_returns_empty_for_invalid(self):
        self.assertEqual(_get_gateway_subnet_candidates(None), [])
        self.assertEqual(_get_gateway_subnet_candidates(""), [])

    def test_limits_count(self):
        candidates = _get_gateway_subnet_candidates("10.0.0.1", count=2)
        self.assertTrue(len(candidates) <= 2)


class TestDiscoverPortalBase(unittest.TestCase):
    @patch("campus_auto_login._test_portal_candidate")
    def test_returns_configured_if_reachable(self, mock_test):
        mock_test.return_value = True
        result = discover_portal_base("http://10.200.84.3")
        self.assertEqual(result, "http://10.200.84.3")

    @patch("campus_auto_login._get_default_gateway")
    @patch("campus_auto_login._test_portal_candidate")
    @patch("campus_auto_login.fetch_direct_raw")
    def test_tries_discovery_when_unreachable(self, mock_raw, mock_test, mock_gw):
        # Configured portal fails
        mock_test.side_effect = [False, False, False, True]
        mock_raw.side_effect = OSError("fail")
        mock_gw.return_value = "10.211.223.1"
        result = discover_portal_base("http://10.200.84.3", timeout=1)
        # Should have tried multiple candidates
        self.assertTrue(mock_test.call_count > 1)

    @patch("campus_auto_login._get_default_gateway")
    @patch("campus_auto_login._test_portal_candidate")
    @patch("campus_auto_login.fetch_direct_raw")
    def test_returns_configured_if_nothing_found(self, mock_raw, mock_test, mock_gw):
        mock_test.return_value = False
        mock_raw.side_effect = OSError("fail")
        mock_gw.return_value = None
        result = discover_portal_base("http://10.200.84.3", timeout=1)
        self.assertEqual(result, "http://10.200.84.3")


if __name__ == "__main__":
    unittest.main()
