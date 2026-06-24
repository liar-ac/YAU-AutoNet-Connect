#!/usr/bin/env python3
"""Lightweight tests for campus_auto_login (no network required)."""
import os
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import campus_auto_login as campus_module
from campus_auto_login import (
    DIRECT_OPENER,
    account_prefix,
    client_info_from_status,
    diagnose_portal_connectivity,
    discover_portal_base,
    ensure_wifi_interface_enabled,
    ensure_process_proxy_bypass_for_portal,
    eportal_login_url,
    fetch_direct_text,
    fetch_direct_raw,
    fetch_text_with_retry,
    fetch_portal_text_resilient,
    get_status,
    invoke_jsonp,
    invoke_url_jsonp,
    jsonp_to_obj,
    login_once,
    normalize_interval,
    open_direct,
    query_string,
    reconnect_campus_wifi,
    wait_for_portal_ready,
    __version__,
    _extract_portal_from_url,
    _find_config_file,
    _get_portal_route_info,
    _is_socket_unreachable_error,
    _test_portal_candidate,
    _get_default_gateway,
    _get_gateway_subnet_candidates,
    _is_wifi_power_off_error,
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

    def test_exact_min_boundary(self):
        self.assertEqual(normalize_interval(5), 5)

    def test_exact_max_boundary(self):
        self.assertEqual(normalize_interval(30), 30)

    def test_just_below_min(self):
        self.assertEqual(normalize_interval(4), 5)


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


class TestResilientCachedSource(unittest.TestCase):
    @patch("campus_auto_login._load_campus_route_cache")
    @patch("campus_auto_login.fetch_direct_with_source")
    @patch("campus_auto_login.fetch_direct_text")
    def test_uses_cached_source_after_raw_direct_fails(self, mock_direct, mock_source, mock_load):
        old_cache = dict(campus_module._campus_route_cache)
        old_preferred = campus_module._preferred_source_ip[0]
        try:
            campus_module._campus_route_cache.update({
                "ifIndex": "6",
                "gateway": "10.211.0.1",
                "source_ip": "10.211.223.248",
                "metric": "0",
                "alias": "WLAN",
            })
            campus_module._preferred_source_ip[0] = None
            mock_direct.side_effect = OSError("no route")
            mock_source.return_value = 'cb({"result":1})'

            text, layer = fetch_portal_text_resilient(
                "http://10.200.84.3/drcom/chkstatus?callback=cb",
                timeout=3,
            )

            self.assertEqual(text, 'cb({"result":1})')
            self.assertEqual(layer, "cached_source(10.211.223.248)")
            mock_source.assert_called_once()
            self.assertEqual(mock_source.call_args[0][1], "10.211.223.248")
        finally:
            campus_module._campus_route_cache.clear()
            campus_module._campus_route_cache.update(old_cache)
            campus_module._preferred_source_ip[0] = old_preferred


class TestInvokeJsonpUsesResilientFetch(unittest.TestCase):
    @patch("campus_auto_login.fetch_portal_text_resilient")
    def test_invoke_jsonp_calls_resilient(self, mock_fetch):
        mock_fetch.return_value = ('callback({"result":1});', "raw_direct")
        result = invoke_jsonp("http://10.200.84.3", "/drcom/chkstatus")
        self.assertEqual(result["result"], 1)
        mock_fetch.assert_called_once()

    @patch("campus_auto_login.fetch_portal_text_resilient")
    def test_invoke_url_jsonp_calls_resilient(self, mock_fetch):
        mock_fetch.return_value = ('callback({"result":"ok"});', "raw_direct")
        result = invoke_url_jsonp(
            "http://10.200.84.3:801/eportal/portal/login",
            [("user_account", "test")],
            portal_base="http://10.200.84.3",
        )
        self.assertEqual(result["result"], "ok")
        mock_fetch.assert_called_once()

    @patch("campus_auto_login.fetch_portal_text_resilient")
    def test_invoke_url_jsonp_referer_uses_portal_base(self, mock_fetch):
        mock_fetch.return_value = ('callback({"result":"ok"});', "raw_direct")
        invoke_url_jsonp(
            "http://10.200.84.3:801/eportal/portal/login",
            [],
            portal_base="http://10.200.100.1",
        )
        call_kwargs = mock_fetch.call_args[1]
        self.assertIn("headers", call_kwargs)
        self.assertEqual(call_kwargs["headers"]["Referer"], "http://10.200.100.1/")


class TestDiagnosePortalConnectivity(unittest.TestCase):
    @patch("campus_auto_login._detect_virtual_adapters", return_value=[])
    @patch("campus_auto_login._get_portal_route_info")
    @patch("campus_auto_login.socket.create_connection")
    @patch("campus_auto_login.fetch_direct_text")
    def test_returns_lines(self, mock_fetch, mock_sock, mock_route, mock_vnet):
        mock_sock.return_value = MagicMock()
        mock_route.return_value = {"ifIndex": "6", "alias": "WLAN", "sourceIP": "10.211.223.248", "nextHop": "10.211.0.1", "metric": "0"}
        mock_fetch.return_value = 'cb({"result":1})'
        lines = diagnose_portal_connectivity("http://10.200.84.3")
        self.assertTrue(len(lines) > 5)
        self.assertTrue(any("Portal" in l for l in lines))
        self.assertTrue(any("Socket" in l for l in lines))

    @patch("campus_auto_login._detect_virtual_adapters", return_value=[])
    @patch("campus_auto_login._get_portal_route_info")
    @patch("campus_auto_login.socket.create_connection", side_effect=OSError("test error"))
    @patch("campus_auto_login.fetch_direct_text", side_effect=OSError("test"))
    def test_socket_fail_shown(self, mock_fetch, mock_sock, mock_route, mock_vnet):
        mock_route.return_value = {"ifIndex": None, "alias": None, "sourceIP": None, "nextHop": None, "metric": None}
        lines = diagnose_portal_connectivity("http://10.200.84.3")
        self.assertTrue(any("失败" in l for l in lines))


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
    @patch("campus_auto_login.subprocess.run")
    def test_parses_gateway(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"10.211.223.1\n",
            stderr=b"",
        )
        gw = _get_default_gateway()
        self.assertEqual(gw, "10.211.223.1")

    @patch("campus_auto_login.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.side_effect = Exception("fail")
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


class TestIsSocketUnreachableError(unittest.TestCase):
    def test_winerror_10065(self):
        self.assertTrue(_is_socket_unreachable_error(OSError("[WinError 10065] error")))

    def test_network_unreachable(self):
        self.assertTrue(_is_socket_unreachable_error(OSError("Network is unreachable")))

    def test_timed_out(self):
        self.assertTrue(_is_socket_unreachable_error(TimeoutError("Connection timed out")))

    def test_http_error_not_socket(self):
        self.assertFalse(_is_socket_unreachable_error(OSError("HTTP 500 Internal Server Error")))

    def test_value_error_not_socket(self):
        self.assertFalse(_is_socket_unreachable_error(ValueError("bad response")))


class TestFetchTextWithRetry(unittest.TestCase):
    @patch("campus_auto_login.fetch_direct_text")
    def test_succeeds_first_try(self, mock_fetch):
        mock_fetch.return_value = '{"result":1}'
        text, attempt = fetch_text_with_retry("http://test/path")
        self.assertEqual(text, '{"result":1}')
        self.assertEqual(attempt, 1)
        self.assertEqual(mock_fetch.call_count, 1)

    @patch("campus_auto_login.time.sleep")
    @patch("campus_auto_login.fetch_direct_text")
    def test_retries_on_socket_error(self, mock_fetch, mock_sleep):
        mock_fetch.side_effect = [
            OSError("[WinError 10065] error"),
            '{"result":1}',
        ]
        text, attempt = fetch_text_with_retry("http://test/path", retries=[0, 0])
        self.assertEqual(text, '{"result":1}')
        self.assertEqual(attempt, 2)

    @patch("campus_auto_login.time.sleep")
    @patch("campus_auto_login.fetch_direct_text")
    def test_raises_after_all_retries(self, mock_fetch, mock_sleep):
        mock_fetch.side_effect = OSError("[WinError 10065] error")
        with self.assertRaises(OSError):
            fetch_text_with_retry("http://test/path", retries=[0, 0])
        self.assertEqual(mock_fetch.call_count, 3)  # initial + 2 retries

    @patch("campus_auto_login.time.sleep")
    @patch("campus_auto_login.fetch_direct_text")
    def test_non_socket_error_not_retried(self, mock_fetch, mock_sleep):
        mock_fetch.side_effect = ValueError("bad json")
        with self.assertRaises(ValueError):
            fetch_text_with_retry("http://test/path", retries=[0, 0])
        self.assertEqual(mock_fetch.call_count, 1)  # no retry for non-socket errors


class TestGetStatus(unittest.TestCase):
    @patch("campus_auto_login.fetch_portal_text_resilient")
    def test_online_state(self, mock_fetch):
        mock_fetch.return_value = ('callback({"result":1})', "raw_direct")
        status = get_status("http://10.200.84.3")
        self.assertEqual(status["state"], "online")
        self.assertTrue(status["online"])
        self.assertTrue(status["reachable"])
        self.assertEqual(status["attempts"], 1)
        self.assertEqual(status["layer"], "raw_direct")

    @patch("campus_auto_login.fetch_portal_text_resilient")
    def test_offline_state(self, mock_fetch):
        mock_fetch.return_value = ('callback({"result":0})', "raw_direct")
        status = get_status("http://10.200.84.3")
        self.assertEqual(status["state"], "offline")
        self.assertFalse(status["online"])
        self.assertTrue(status["reachable"])

    @patch("campus_auto_login.fetch_portal_text_resilient")
    def test_network_not_ready_on_socket_error(self, mock_fetch):
        mock_fetch.side_effect = OSError("[WinError 10065] error")
        status = get_status("http://10.200.84.3")
        self.assertEqual(status["state"], "network_not_ready")
        self.assertFalse(status["reachable"])
        self.assertTrue(status["is_network_unreachable"])

    @patch("campus_auto_login.fetch_portal_text_resilient")
    def test_portal_unreachable_on_http_error(self, mock_fetch):
        mock_fetch.side_effect = OSError("HTTP 500 Internal Server Error")
        status = get_status("http://10.200.84.3")
        self.assertEqual(status["state"], "portal_unreachable")
        self.assertFalse(status["reachable"])
        self.assertFalse(status["is_network_unreachable"])


class TestLoginOnceNetworkNotReady(unittest.TestCase):
    @patch("campus_auto_login.write_log")
    @patch("campus_auto_login.get_status")
    def test_network_not_ready_increments_failure(self, mock_status, mock_log):
        mock_status.return_value = {"state": "network_not_ready", "reachable": False, "online": False, "raw": None, "error": "test", "is_network_unreachable": True}
        args = MagicMock()
        args.log = MagicMock()
        args.check = False
        config = {"portal_base": "http://10.200.84.3"}
        failure_state = {"consecutive_failures": 0}
        result = login_once(config, args, failure_state=failure_state)
        self.assertFalse(result)
        self.assertEqual(failure_state["consecutive_failures"], 1)

    @patch("campus_auto_login._cache_campus_route")
    @patch("campus_auto_login.write_log")
    @patch("campus_auto_login.get_status")
    def test_success_resets_failure_count(self, mock_status, mock_log, mock_cache):
        mock_status.return_value = {"state": "online", "reachable": True, "online": True, "raw": {}, "error": None, "attempts": 1}
        args = MagicMock()
        args.log = MagicMock()
        config = {"portal_base": "http://10.200.84.3"}
        failure_state = {"consecutive_failures": 5}
        result = login_once(config, args, failure_state=failure_state)
        self.assertTrue(result)
        self.assertEqual(failure_state["consecutive_failures"], 0)

    @patch("campus_auto_login._cache_campus_route")
    @patch("campus_auto_login.write_log")
    @patch("campus_auto_login.get_status")
    def test_reachable_layer_without_attempts_does_not_crash(self, mock_status, mock_log, mock_cache):
        mock_status.return_value = {
            "state": "online",
            "reachable": True,
            "online": True,
            "raw": {},
            "error": None,
            "layer": "raw_direct",
        }
        args = MagicMock()
        args.log = MagicMock()
        config = {"portal_base": "http://10.200.84.3"}
        self.assertTrue(login_once(config, args, failure_state={"consecutive_failures": 1}))
        messages = [call_args[0][1] for call_args in mock_log.call_args_list]
        self.assertTrue(any("已连接" in m for m in messages))


class TestWaitForPortalReady(unittest.TestCase):
    @patch("campus_auto_login.get_status")
    def test_returns_immediately_if_ready(self, mock_status):
        mock_status.return_value = {"state": "online", "reachable": True, "online": True}
        result = wait_for_portal_ready("http://10.200.84.3", timeout_seconds=10, interval=1)
        self.assertIsNotNone(result)
        self.assertEqual(result["state"], "online")

    @patch("campus_auto_login.time.sleep")
    @patch("campus_auto_login.get_status")
    def test_returns_none_on_timeout(self, mock_status, mock_sleep):
        mock_status.return_value = {"state": "network_not_ready", "reachable": False, "online": False}
        result = wait_for_portal_ready("http://10.200.84.3", timeout_seconds=2, interval=1)
        self.assertIsNone(result)

    @patch("campus_auto_login.time.sleep")
    @patch("campus_auto_login.get_status")
    def test_recovers_after_transient_failure(self, mock_status, mock_sleep):
        mock_status.side_effect = [
            {"state": "network_not_ready", "reachable": False, "online": False},
            {"state": "offline", "reachable": True, "online": False},
        ]
        result = wait_for_portal_ready("http://10.200.84.3", timeout_seconds=10, interval=1)
        self.assertIsNotNone(result)
        self.assertEqual(result["state"], "offline")

    @patch("campus_auto_login.time.sleep")
    @patch("campus_auto_login.reconnect_campus_wifi")
    @patch("campus_auto_login.get_status")
    def test_requests_wifi_reconnect_on_network_not_ready(self, mock_status, mock_reconnect, mock_sleep):
        mock_status.side_effect = [
            {"state": "network_not_ready", "reachable": False, "online": False},
            {"state": "offline", "reachable": True, "online": False},
        ]
        mock_reconnect.return_value = True
        result = wait_for_portal_ready(
            "http://10.200.84.3",
            timeout_seconds=10,
            interval=1,
            campus_ssid="YADX-STU",
        )
        self.assertEqual(result["state"], "offline")
        mock_reconnect.assert_called_once()
        self.assertEqual(mock_reconnect.call_args[0][0], "YADX-STU")

    @patch("campus_auto_login.time.sleep")
    @patch("campus_auto_login.reconnect_campus_wifi")
    @patch("campus_auto_login.get_status")
    def test_reconnect_attempt_is_limited_per_wait_window(self, mock_status, mock_reconnect, mock_sleep):
        mock_status.return_value = {"state": "network_not_ready", "reachable": False, "online": False}
        mock_reconnect.return_value = False
        result = wait_for_portal_ready(
            "http://10.200.84.3",
            timeout_seconds=3,
            interval=1,
            campus_ssid="YADX-STU",
        )
        self.assertIsNone(result)
        mock_reconnect.assert_called_once()


class TestReconnectCampusWifi(unittest.TestCase):
    @patch("campus_auto_login.subprocess.run")
    def test_reconnect_uses_netsh_profile(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        self.assertTrue(reconnect_campus_wifi("YADX-STU"))
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0], ["netsh", "wlan", "connect", "name=YADX-STU"])

    @patch("campus_auto_login.subprocess.run")
    @patch("campus_auto_login.get_current_wifi_ssid", return_value="")
    @patch("campus_auto_login._load_campus_route_cache", return_value={"ssid": "?"})
    def test_reconnect_falls_back_to_yadx_profile(self, mock_cache, mock_ssid, mock_run):
        profiles = "    All User Profile     : YADX-STU\r\n".encode("utf-8")
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=profiles, stderr=b""),
            MagicMock(returncode=0, stdout=b"", stderr=b""),
        ]
        self.assertTrue(reconnect_campus_wifi(""))
        self.assertEqual(mock_run.call_args_list[-1][0][0], ["netsh", "wlan", "connect", "name=YADX-STU"])

    @patch("campus_auto_login.time.sleep")
    @patch("campus_auto_login.ensure_wifi_interface_enabled")
    @patch("campus_auto_login.subprocess.run")
    def test_reconnect_enables_wifi_when_radio_is_powered_off(self, mock_run, mock_enable, mock_sleep):
        mock_run.side_effect = [
            MagicMock(
                returncode=1,
                stdout=b"",
                stderr=b"WlanGetAvailableNetworkList failed because radio is off",
            ),
            MagicMock(returncode=0, stdout=b"", stderr=b""),
        ]
        self.assertTrue(reconnect_campus_wifi("YADX-STU"))
        mock_enable.assert_called_once()
        self.assertEqual(mock_run.call_count, 2)

    def test_detects_chinese_wifi_power_off_error(self):
        self.assertTrue(_is_wifi_power_off_error("无线局域网接口电源关闭，它不支持请求的操作。"))

    @patch("campus_auto_login._enable_wifi_software_radio", return_value=True)
    @patch("campus_auto_login._get_wifi_adapter_name", return_value="WLAN")
    @patch("campus_auto_login.subprocess.run")
    def test_ensure_wifi_interface_enabled_runs_enable_steps(self, mock_run, mock_adapter, mock_radio):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        self.assertTrue(ensure_wifi_interface_enabled())
        commands = [call_args[0][0] for call_args in mock_run.call_args_list]
        self.assertIn(["netsh", "interface", "set", "interface", "name=WLAN", "admin=enabled"], commands)
        self.assertIn(["netsh", "wlan", "set", "autoconfig", "enabled=yes", "interface=WLAN"], commands)
        mock_radio.assert_called_once()


class TestEnsureProcessProxyBypass(unittest.TestCase):
    def test_sets_no_proxy(self):
        old_no_proxy = os.environ.get("NO_PROXY", "")
        old_no_proxy_lower = os.environ.get("no_proxy", "")
        try:
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)
            ensure_process_proxy_bypass_for_portal(["10.200.84.3"])
            self.assertIn("10.200.84.3", os.environ.get("NO_PROXY", ""))
            self.assertIn("10.200.84.3", os.environ.get("no_proxy", ""))
            self.assertIn("localhost", os.environ.get("NO_PROXY", ""))
        finally:
            os.environ["NO_PROXY"] = old_no_proxy
            os.environ["no_proxy"] = old_no_proxy_lower

    def test_appends_to_existing(self):
        old_no_proxy = os.environ.get("NO_PROXY", "")
        try:
            os.environ["NO_PROXY"] = "example.com"
            ensure_process_proxy_bypass_for_portal(["10.200.84.3"])
            val = os.environ["NO_PROXY"]
            self.assertIn("example.com", val)
            self.assertIn("10.200.84.3", val)
        finally:
            os.environ["NO_PROXY"] = old_no_proxy


class TestGetPortalRouteInfo(unittest.TestCase):
    def test_parses_route_output(self):
        route_output = (
            "===========================================================================\n"
            "Active Routes:\n"
            "     Network Destination        Netmask          Gateway       Interface  Metric\n"
            "          0.0.0.0          0.0.0.0      10.211.0.1   10.211.223.248     35\n"
            "===========================================================================\n"
        )
        with patch("campus_auto_login.subprocess.run") as mock_run:
            # First call: route print, second call: PowerShell (optional)
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=route_output.encode(), stderr=b""),
                MagicMock(returncode=0, stdout=b"", stderr=b""),
            ]
            info = _get_portal_route_info("10.200.84.3")
            self.assertEqual(info["nextHop"], "10.211.0.1")
            self.assertEqual(info["sourceIP"], "10.211.223.248")
            self.assertEqual(info["metric"], "35")

    def test_returns_empty_on_failure(self):
        with patch("campus_auto_login.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
            info = _get_portal_route_info("10.200.84.3")
            self.assertIsNone(info["nextHop"])


class TestDiagnoseContainsRouteInfo(unittest.TestCase):
    @patch("campus_auto_login._detect_virtual_adapters", return_value=[])
    @patch("campus_auto_login._get_portal_route_info")
    @patch("campus_auto_login.socket.create_connection")
    @patch("campus_auto_login.fetch_direct_text")
    def test_output_contains_route_fields(self, mock_fetch, mock_sock, mock_route, mock_vnet):
        mock_route.return_value = {"ifIndex": "6", "alias": "WLAN", "sourceIP": "10.211.223.248", "nextHop": "10.211.0.1", "metric": "0"}
        mock_sock.return_value = MagicMock()
        mock_fetch.return_value = 'cb({"result":1})'
        lines = diagnose_portal_connectivity("http://10.200.84.3")
        text = "\n".join(lines)
        self.assertIn("Portal路由接口: WLAN", text)
        self.assertIn("Portal路由源IP: 10.211.223.248", text)
        self.assertIn("Portal路由下一跳: 10.211.0.1", text)
        self.assertIn("NO_PROXY includes portal subnet", text)


class TestDiscoverPortalPriority(unittest.TestCase):
    @patch("campus_auto_login._get_default_gateway")
    @patch("campus_auto_login._test_portal_candidate")
    def test_tries_default_portal_before_ncsi(self, mock_test, mock_gw):
        # Configured portal fails, default portal succeeds
        mock_test.side_effect = lambda url, **kw: url == "http://10.200.84.3"
        mock_gw.return_value = None
        result = discover_portal_base("http://10.200.100.1", timeout=1)
        self.assertEqual(result, "http://10.200.84.3")

    @patch("campus_auto_login._get_default_gateway")
    @patch("campus_auto_login._test_portal_candidate")
    def test_returns_configured_if_reachable(self, mock_test, mock_gw):
        mock_test.return_value = True
        mock_gw.return_value = None
        result = discover_portal_base("http://10.200.84.3", timeout=1)
        self.assertEqual(result, "http://10.200.84.3")


class TestFindConfigFile(unittest.TestCase):
    def test_returns_none_when_no_config_found(self):
        """Returns None when no config exists anywhere."""
        with patch.object(Path, "exists", return_value=False):
            result = _find_config_file(campus_module.DEFAULT_CONFIG)
            self.assertIsNone(result)

    def test_read_config_searches_appdata_fallback(self):
        """read_config should find config in %APPDATA% when exe dir has none."""
        import tempfile, json
        fake_config = {"username": "test", "password_dpapi": "fake", "portal_base": "http://10.200.84.3"}
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # Make SCRIPT_DIR point to a subdir with no configs
            subdir = td_path / "app"
            subdir.mkdir()
            fake_path = td_path / "campus_login_py.config.json"
            fake_path.write_text(json.dumps(fake_config), encoding="utf-8")
            with patch.object(campus_module, "SCRIPT_DIR", subdir), \
                 patch.object(campus_module, "DEFAULT_CONFIG", subdir / "campus_login_py.config.json"), \
                 patch.object(campus_module, "DEFAULT_PS_CONFIG", subdir / "campus_login.config.json"), \
                 patch.object(campus_module, "_USER_CONFIG", fake_path), \
                 patch.object(campus_module, "_USER_PS_CONFIG", td_path / "nonexistent_user_ps.json"), \
                 patch("campus_auto_login.Path") as mock_path_cls:
                # Make Path.cwd() return td_path
                mock_path_cls.cwd.return_value = td_path
                mock_path_cls.side_effect = lambda *a: Path(*a)
                result = campus_module.read_config(campus_module.DEFAULT_CONFIG)
                self.assertEqual(result["username"], "test")
                self.assertEqual(result["config_format"], "python")

    def test_read_config_searches_appdata_ps_fallback(self):
        """read_config should find PowerShell config in %APPDATA% as last resort."""
        import tempfile, json
        fake_config = {"Username": "test", "Password": "01000000deadbeef", "PortalBase": "http://10.200.84.3"}
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            subdir = td_path / "app"
            subdir.mkdir()
            fake_path = td_path / "campus_login.config.json"
            fake_path.write_text(json.dumps(fake_config), encoding="utf-8")
            with patch.object(campus_module, "SCRIPT_DIR", subdir), \
                 patch.object(campus_module, "DEFAULT_CONFIG", subdir / "campus_login_py.config.json"), \
                 patch.object(campus_module, "DEFAULT_PS_CONFIG", subdir / "campus_login.config.json"), \
                 patch.object(campus_module, "_USER_CONFIG", td_path / "nonexistent_user_py.json"), \
                 patch.object(campus_module, "_USER_PS_CONFIG", fake_path), \
                 patch("campus_auto_login.Path") as mock_path_cls:
                mock_path_cls.cwd.return_value = td_path
                mock_path_cls.side_effect = lambda *a: Path(*a)
                result = campus_module.read_config(campus_module.DEFAULT_CONFIG)
                self.assertEqual(result["username"], "test")
                self.assertEqual(result["config_format"], "powershell")


class TestWaitForNetworkReadyEnhanced(unittest.TestCase):
    """Test enhanced boot-time network ready logic with proactive Wi-Fi reconnection."""

    def test_triggers_wifi_reconnect_on_boot_when_adapter_exists_but_no_wifi(self):
        """Should trigger Wi-Fi reconnect when physical adapter exists but no SSID detected."""
        with patch("campus_auto_login.network_ready", side_effect=[False, False, True]), \
             patch("campus_auto_login._seconds_since_boot", return_value=60), \
             patch("campus_auto_login._has_physical_adapter", return_value=True), \
             patch("campus_auto_login._get_physical_adapter_ips", return_value=[
                 ("192.168.1.100", 10, "WLAN", "Wi-Fi Adapter", False)
             ]), \
             patch("campus_auto_login.get_current_wifi_ssid", return_value=""), \
             patch("campus_auto_login.reconnect_campus_wifi", return_value=True) as mock_reconnect:
            result = campus_module.wait_for_network_ready(
                portal_host="10.200.84.3",
                timeout_seconds=30,
                check_interval=1,
                stable_seconds=1,
                campus_ssid="YADX-STU"
            )
            self.assertTrue(result)
            mock_reconnect.assert_called_once()

    def test_waits_for_dhcp_when_wifi_connected_but_no_internal_ip(self):
        """Should wait for DHCP when Wi-Fi is connected but no internal IP yet."""
        with patch("campus_auto_login.network_ready", side_effect=[False, False, True]), \
             patch("campus_auto_login._seconds_since_boot", return_value=60), \
             patch("campus_auto_login._has_physical_adapter", return_value=True), \
             patch("campus_auto_login._get_physical_adapter_ips", side_effect=[
                 [("169.254.1.100", 10, "WLAN", "Wi-Fi Adapter", False)],  # APIPA, no DHCP yet
                 [("10.200.100.50", 10, "WLAN", "Wi-Fi Adapter", False)]   # Got internal IP
             ]), \
             patch("campus_auto_login.get_current_wifi_ssid", return_value="YADX-STU"), \
             patch("campus_auto_login.reconnect_campus_wifi") as mock_reconnect:
            result = campus_module.wait_for_network_ready(
                portal_host="10.200.84.3",
                timeout_seconds=30,
                check_interval=1,
                stable_seconds=1,
                campus_ssid="YADX-STU"
            )
            self.assertTrue(result)
            # Should not trigger reconnect since Wi-Fi is already connected
            mock_reconnect.assert_not_called()

    def test_extends_timeout_to_240s_on_boot_without_adapter(self):
        """Should extend timeout to 240 seconds when booting without physical adapter."""
        with patch("campus_auto_login.network_ready", side_effect=[False] * 10 + [True]), \
             patch("campus_auto_login._seconds_since_boot", return_value=60), \
             patch("campus_auto_login._has_physical_adapter", return_value=False), \
             patch("campus_auto_login._get_physical_adapter_ips", return_value=[]), \
             patch("campus_auto_login.get_current_wifi_ssid", return_value=""), \
             patch("campus_auto_login.reconnect_campus_wifi"):
            start = time.time()
            result = campus_module.wait_for_network_ready(
                portal_host="10.200.84.3",
                timeout_seconds=10,  # Will be extended to 240s
                check_interval=1,
                stable_seconds=1
            )
            elapsed = time.time() - start
            self.assertTrue(result)
            # Should have extended timeout (not fail at 10s)
            self.assertLess(elapsed, 20)  # But succeeded early

    def test_does_not_trigger_reconnect_outside_boot_window(self):
        """Should not trigger proactive Wi-Fi reconnect after boot window expires."""
        with patch("campus_auto_login.network_ready", side_effect=[False, True]), \
             patch("campus_auto_login._seconds_since_boot", return_value=400), \
             patch("campus_auto_login._get_physical_adapter_ips", return_value=[
                 ("192.168.1.100", 10, "WLAN", "Wi-Fi Adapter", False)
             ]), \
             patch("campus_auto_login.get_current_wifi_ssid", return_value=""), \
             patch("campus_auto_login.reconnect_campus_wifi") as mock_reconnect:
            result = campus_module.wait_for_network_ready(
                portal_host="10.200.84.3",
                timeout_seconds=10,
                check_interval=1,
                stable_seconds=1
            )
            self.assertTrue(result)
            # Should not reconnect outside boot window (>300s since boot)
            mock_reconnect.assert_not_called()

    def test_skips_reconnect_when_wifi_already_connected(self):
        """Should skip Wi-Fi reconnect when already connected to correct SSID."""
        with patch("campus_auto_login.network_ready", side_effect=[False, True]), \
             patch("campus_auto_login._seconds_since_boot", return_value=60), \
             patch("campus_auto_login._get_physical_adapter_ips", return_value=[
                 ("10.200.100.50", 10, "WLAN", "Wi-Fi Adapter", False)
             ]), \
             patch("campus_auto_login.get_current_wifi_ssid", return_value="YADX-STU"), \
             patch("campus_auto_login.reconnect_campus_wifi") as mock_reconnect:
            result = campus_module.wait_for_network_ready(
                portal_host="10.200.84.3",
                timeout_seconds=10,
                check_interval=1,
                stable_seconds=1,
                campus_ssid="YADX-STU"
            )
            self.assertTrue(result)
            # Already connected with internal IP, no need to reconnect
            mock_reconnect.assert_not_called()


class TestIsPrivateIP(unittest.TestCase):
    """Test RFC 1918 private IP detection."""

    def test_class_a_private_network(self):
        """Should recognize 10.0.0.0/8 range."""
        self.assertTrue(campus_module._is_private_ip("10.0.0.1"))
        self.assertTrue(campus_module._is_private_ip("10.255.255.254"))
        self.assertTrue(campus_module._is_private_ip("10.200.84.3"))

    def test_class_b_private_network(self):
        """Should recognize 172.16.0.0/12 range (172.16.0.0 to 172.31.255.255)."""
        self.assertTrue(campus_module._is_private_ip("172.16.0.1"))
        self.assertTrue(campus_module._is_private_ip("172.17.0.1"))
        self.assertTrue(campus_module._is_private_ip("172.20.0.1"))
        self.assertTrue(campus_module._is_private_ip("172.31.255.254"))
        # Outside range
        self.assertFalse(campus_module._is_private_ip("172.15.255.254"))
        self.assertFalse(campus_module._is_private_ip("172.32.0.1"))

    def test_class_c_private_network(self):
        """Should recognize 192.168.0.0/16 range."""
        self.assertTrue(campus_module._is_private_ip("192.168.0.1"))
        self.assertTrue(campus_module._is_private_ip("192.168.255.254"))
        self.assertTrue(campus_module._is_private_ip("192.168.1.100"))

    def test_cgn_network(self):
        """Should recognize 100.64.0.0/10 Carrier-Grade NAT range."""
        self.assertTrue(campus_module._is_private_ip("100.64.0.1"))
        self.assertTrue(campus_module._is_private_ip("100.127.255.254"))

    def test_public_ips(self):
        """Should reject public IPs."""
        self.assertFalse(campus_module._is_private_ip("8.8.8.8"))
        self.assertFalse(campus_module._is_private_ip("1.1.1.1"))
        self.assertFalse(campus_module._is_private_ip("203.0.113.1"))

    def test_link_local(self):
        """Should reject link-local (169.254.x.x)."""
        self.assertFalse(campus_module._is_private_ip("169.254.1.1"))

    def test_loopback(self):
        """Should reject loopback (127.x.x.x)."""
        self.assertFalse(campus_module._is_private_ip("127.0.0.1"))

    def test_invalid_ip(self):
        """Should handle invalid IPs gracefully."""
        self.assertFalse(campus_module._is_private_ip("not.an.ip"))
        self.assertFalse(campus_module._is_private_ip("172.invalid.0.1"))


if __name__ == "__main__":
    unittest.main()
