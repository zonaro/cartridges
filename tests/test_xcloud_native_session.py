import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cartridges.xcloud_native import auth, session

BASE = "https://eastus.gssv-play-prod.xboxlive.com"
GS = "GS-TOKEN"
LPT = "LPT-VALUE"
SESSION_PATH = "/v5/sessions/cloud/play/abc-123"
SRTP_KEY = "super-secret-srtp-key"

SDP_ANSWER = {
    "chat": 1,
    "control": 3,
    "input": 9,
    "message": 1,
    "messageType": "answer",
    "status": "success",
    "sdp": "v=0\r\nANSWER\r\n",
}

SERVER_DETAILS = {
    "ipAddress": "10.0.0.5",
    "port": 443,
    "ipV4Address": "10.0.0.5",
    "ipV4Port": 443,
    "stunServerAddress": "stun.example:3478",
    "srtp": {"key": SRTP_KEY},
}

CONFIGURATION = {
    "keepAlivePulseInSeconds": 30,
    "serverDetails": SERVER_DETAILS,
}


def _tokens(offering="xgpuweb", regions=None):
    return auth.StreamingTokens(
        gs_token=GS,
        market="pt-BR",
        regions=regions if regions is not None else [],
        offering=offering,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _login(offering="xgpuweb", regions=None):
    return auth.LoginResult(
        streaming=_tokens(offering, regions), lpt=LPT, refresh_token="RT"
    )


def _resp(payload=None, code=200):
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return code, body, {}


def _pending(code=204):
    return code, b"", {}


def _noop_sleep(_seconds):
    return None


def _replay(responses):
    """Respostas em sequência; a última se repete quando acabam."""
    remaining = list(responses)

    def route(_method, _url, _data):
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return route


class FakeHttp:
    """Mock no limite do módulo: substitui `session._http_request`.

    Rotas são tuplas `(método, fragmento_da_url, resposta)`; vence a rota com o
    fragmento mais longo que casar, então a ordem de declaração não importa. A
    resposta pode ser uma tripla ou um callable `(method, url, data) -> tripla`.
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, data=None, headers=None, timeout=20):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "data": data,
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        matches = [
            (len(fragment), response)
            for route_method, fragment, response in self.routes
            if route_method == method and fragment in url
        ]
        if not matches:
            raise AssertionError(f"chamada inesperada: {method} {url}")
        response = max(matches, key=lambda match: match[0])[1]
        return response(method, url, data) if callable(response) else response

    def urls(self, method=None):
        return [
            call["url"]
            for call in self.calls
            if method is None or call["method"] == method
        ]

    def payload(self, index):
        return json.loads(self.calls[index]["data"].decode("utf-8"))


class CatalogTests(unittest.TestCase):
    def test_list_titles_uses_bearer_and_parses_details(self):
        http = FakeHttp(
            [
                (
                    "GET",
                    "/v2/titles",
                    _resp({
                        "totalItems": 1,
                        "continuationToken": "next",
                        "results": [{
                            "titleId": "TITLE-A",
                            "details": {
                                "productId": "PRODUCT-A",
                                "xboxTitleId": 42,
                                "hasEntitlement": False,
                                "blockedByFamilySafety": True,
                            },
                        }],
                    }),
                )
            ]
        )
        with patch.object(session, "_http_request", side_effect=http):
            page = session.list_titles(BASE, GS)

        self.assertEqual(http.calls[0]["headers"]["Authorization"], f"Bearer {GS}")
        self.assertEqual(http.calls[0]["headers"]["X-Gssv-Client"], "XboxComBrowser")
        self.assertEqual(http.calls[0]["url"], f"{BASE}/v2/titles?mr=25")
        self.assertEqual(page.total_items, 1)
        self.assertEqual(page.continuation_token, "next")
        self.assertEqual(
            page.titles,
            (
                session.Title(
                    title_id="TITLE-A",
                    product_id="PRODUCT-A",
                    xbox_title_id=42,
                    has_entitlement=False,
                    blocked_by_family_safety=True,
                ),
            ),
        )

    def test_list_titles_paginates_with_continuation_token(self):
        http = FakeHttp([("GET", "/v2/titles", _resp({"results": []}))])
        with patch.object(session, "_http_request", side_effect=http):
            session.list_titles(BASE, GS, max=10, continuation_token="CT-2")
        self.assertEqual(http.calls[0]["url"], f"{BASE}/v2/titles?mr=10&ct=CT-2")

    def test_list_mru_uses_its_own_path(self):
        http = FakeHttp([("GET", "/v2/titles/mru", _resp({"results": []}))])
        with patch.object(session, "_http_request", side_effect=http):
            session.list_mru(BASE, GS)
        self.assertEqual(http.calls[0]["url"], f"{BASE}/v2/titles/mru?mr=25")

    def test_waittime_parses_estimates(self):
        http = FakeHttp([("GET", "/v1/waittime/TITLE-A", _resp({
            "estimatedProvisioningTimeInSeconds": 12,
            "estimatedAllocationTimeInSeconds": 3,
            "estimatedTotalWaitTimeInSeconds": 15,
        }))])
        with patch.object(session, "_http_request", side_effect=http):
            wait = session.waittime(BASE, GS, "TITLE-A")

        self.assertEqual(http.urls(), [f"{BASE}/v1/waittime/TITLE-A"])
        self.assertEqual(wait, session.WaitTime(12, 3, 15))

    def test_http_error_is_reported_with_status(self):
        http = FakeHttp([("GET", "/v2/titles", (401, b'{"error":"nope"}', {}))])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionHttpError) as caught:
                session.list_titles(BASE, GS)
        self.assertEqual(caught.exception.status, 401)


class StartSessionTests(unittest.TestCase):
    PLAY_RESPONSE = _resp(
        {"sessionId": "SID-1", "sessionPath": SESSION_PATH, "state": "Provisioning"}
    )

    def test_title_fora_do_offering_vira_erro_tipado(self):
        body = (
            b'{"code":"OfferingDoesNotContainTitle","statusCode":400,'
            b'"message":"Offering XGPUWEB does not contain title TITLE-A"}'
        )
        http = FakeHttp([("POST", "/play", (400, body, {}))])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.TitleNotInOfferingError) as caught:
                session.start_session(BASE, GS, title_id="TITLE-A")
        self.assertEqual(caught.exception.status, 400)
        self.assertIsInstance(caught.exception, session.SessionHttpError)

    def test_resolve_title_id_via_product_id(self):
        page1 = _resp(
            {
                "results": [{"titleId": "OTHER", "details": {"productId": "X"}}],
                "totalItems": 2,
                "continuationToken": "CT1",
            }
        )
        page2 = _resp(
            {
                "results": [
                    {
                        "titleId": "GENSHINIMPACT",
                        "details": {"productId": "9N7TFFRRZCC9"},
                    }
                ],
                "totalItems": 2,
                "continuationToken": "",
            }
        )

        def route(method, url, data):
            if "ct=CT1" in url:
                return page2
            return page1

        http = FakeHttp([("GET", "/v2/titles", route)])
        with patch.object(session, "_http_request", side_effect=http):
            self.assertEqual(
                session.resolve_title_id(BASE, GS, "9N7TFFRRZCC9"), "GENSHINIMPACT"
            )
            self.assertEqual(session.resolve_title_id(BASE, GS, "INEXISTENTE"), "")
            self.assertEqual(session.resolve_title_id(BASE, GS, ""), "")

    def test_cloud_play_payload(self):
        http = FakeHttp([("POST", "/play", self.PLAY_RESPONSE)])
        with patch.object(session, "_http_request", side_effect=http):
            started = session.start_session(BASE, GS, title_id="TITLE-A")

        self.assertEqual(
            started, session.SessionStart("SID-1", SESSION_PATH, "Provisioning")
        )
        self.assertEqual(http.calls[0]["url"], f"{BASE}/v5/sessions/cloud/play")
        self.assertEqual(http.calls[0]["headers"]["Authorization"], f"Bearer {GS}")

        payload = http.payload(0)
        self.assertEqual(payload["titleId"], "TITLE-A")
        self.assertEqual(payload["serverId"], "")
        self.assertEqual(payload["fallbackRegionNames"], [])
        self.assertEqual(payload["systemUpdateGroup"], "")
        self.assertEqual(payload["settings"]["nanoVersion"], session.NANO_VERSION)
        self.assertIs(payload["settings"]["useIceConnection"], False)
        self.assertEqual(payload["settings"]["locale"], "pt-BR")
        self.assertIsInstance(payload["settings"]["timezoneOffsetMinutes"], int)

    def test_home_play_uses_server_id_and_home_path(self):
        http = FakeHttp([("POST", "/play", _resp({
            "sessionId": "SID-2",
            "sessionPath": "/v5/sessions/home/play/def",
            "state": "Provisioning",
        }))])
        with patch.object(session, "_http_request", side_effect=http):
            started = session.start_session(BASE, GS, server_id="CONSOLE-1")

        self.assertEqual(http.calls[0]["url"], f"{BASE}/v5/sessions/home/play")
        self.assertEqual(http.payload(0)["serverId"], "CONSOLE-1")
        self.assertEqual(http.payload(0)["titleId"], "")
        self.assertEqual(started.session_id, "SID-2")

    def test_explicit_platform_overrides_inference(self):
        http = FakeHttp([("POST", "/play", self.PLAY_RESPONSE)])
        with patch.object(session, "_http_request", side_effect=http):
            session.start_session(
                BASE,
                GS,
                title_id="TITLE-A",
                server_id="CONSOLE-1",
                platform=session.PLATFORM_CLOUD,
            )
        self.assertIn("/v5/sessions/cloud/play", http.calls[0]["url"])

    def test_missing_target_is_rejected_before_any_request(self):
        http = FakeHttp([])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(ValueError):
                session.start_session(BASE, GS)
            with self.assertRaises(ValueError):
                session.start_session(BASE, GS, platform=session.PLATFORM_HOME)
        self.assertEqual(http.calls, [])

    def test_play_without_session_path_is_an_error(self):
        http = FakeHttp([("POST", "/play", _resp({"sessionId": "SID"}))])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionError):
                session.start_session(BASE, GS, title_id="TITLE-A")


class PollStateTests(unittest.TestCase):
    def _states(self, *names):
        return FakeHttp([
            ("GET", "/state", _replay([_resp({"state": name}) for name in names]))
        ])

    def test_loops_until_provisioned(self):
        http = self._states("Provisioning", "WaitingForResources", "Provisioned")
        sleeps = []
        with patch.object(session, "_http_request", side_effect=http):
            state = session.poll_state(
                BASE, GS, SESSION_PATH, timeout=30, sleep=sleeps.append
            )

        self.assertEqual(state, "Provisioned")
        self.assertEqual(len(http.calls), 3)
        self.assertEqual(http.urls(), [f"{BASE}{SESSION_PATH}/state"] * 3)
        self.assertEqual(sleeps, [session.STATE_POLL_INTERVAL_SEC] * 2)

    def test_ready_to_connect_stops_the_loop(self):
        http = self._states("WaitingForResources", "ReadyToConnect")
        with patch.object(session, "_http_request", side_effect=http):
            self.assertEqual(
                session.poll_state(BASE, GS, SESSION_PATH, sleep=_noop_sleep),
                "ReadyToConnect",
            )
        self.assertEqual(len(http.calls), 2)

    def test_ready_to_connect_callback_runs_once(self):
        http = self._states("ReadyToConnect", "Provisioned")
        seen = []
        with patch.object(session, "_http_request", side_effect=http):
            session.poll_state(
                BASE,
                GS,
                SESSION_PATH,
                sleep=_noop_sleep,
                on_ready_to_connect=seen.append,
            )
        self.assertEqual(seen, ["ReadyToConnect"])

    def test_failed_state_reports_error_details(self):
        http = FakeHttp([("GET", "/state", _resp({
            "state": "Failed",
            "errorDetails": {"code": "TitleNotEntitled", "message": "sem licença"},
        }))])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionStateError) as caught:
                session.poll_state(BASE, GS, SESSION_PATH, sleep=_noop_sleep)

        self.assertEqual(caught.exception.state, "Failed")
        self.assertIn("TitleNotEntitled", str(caught.exception))
        self.assertIn("sem licença", str(caught.exception))

    def test_error_details_alone_is_enough_to_fail(self):
        http = FakeHttp([("GET", "/state", _resp({
            "state": "Provisioning",
            "errorDetails": {"code": "NoCapacity", "message": "sem recursos"},
        }))])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionStateError):
                session.poll_state(BASE, GS, SESSION_PATH, sleep=_noop_sleep)

    def test_unknown_state_is_treated_as_provisional(self):
        http = self._states("AlgoNovoDoServidor", "Provisioned")
        with patch.object(session, "_http_request", side_effect=http):
            self.assertEqual(
                session.poll_state(BASE, GS, SESSION_PATH, sleep=_noop_sleep),
                "Provisioned",
            )
        self.assertEqual(len(http.calls), 2)

    def test_timeout_names_the_last_state(self):
        http = self._states("Provisioning")
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionTimeoutError) as caught:
                session.poll_state(
                    BASE, GS, SESSION_PATH, timeout=0, sleep=_noop_sleep
                )

        self.assertIn("Provisioning", str(caught.exception))
        self.assertEqual(len(http.calls), 1)

    def test_state_requests_carry_the_bearer_header(self):
        http = self._states("Provisioned")
        with patch.object(session, "_http_request", side_effect=http):
            session.poll_state(BASE, GS, SESSION_PATH, sleep=_noop_sleep)
        self.assertEqual(http.calls[0]["headers"]["Authorization"], f"Bearer {GS}")


class ConnectTests(unittest.TestCase):
    def test_body_carries_lpt_and_header_carries_gs_token(self):
        http = FakeHttp([("POST", "/connect", (202, b"", {}))])
        with patch.object(session, "_http_request", side_effect=http):
            session.connect(BASE, LPT, SESSION_PATH, GS)

        call = http.calls[0]
        self.assertEqual(call["url"], f"{BASE}{SESSION_PATH}/connect")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {GS}")
        self.assertEqual(json.loads(call["data"].decode()), {"userToken": LPT})
        self.assertNotIn(GS, call["data"].decode())

    def test_session_path_without_leading_slash_is_accepted(self):
        http = FakeHttp([("POST", "/connect", (202, b"", {}))])
        with patch.object(session, "_http_request", side_effect=http):
            session.connect(BASE, LPT, "v5/sessions/cloud/play/abc", GS)
        self.assertEqual(
            http.calls[0]["url"], f"{BASE}/v5/sessions/cloud/play/abc/connect"
        )

    def test_empty_tokens_are_rejected(self):
        http = FakeHttp([])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(ValueError):
                session.connect(BASE, "", SESSION_PATH, GS)
            with self.assertRaises(ValueError):
                session.connect(BASE, LPT, SESSION_PATH, "")
        self.assertEqual(http.calls, [])

    def test_unexpected_status_raises(self):
        http = FakeHttp([("POST", "/connect", (403, b"{}", {}))])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionHttpError):
                session.connect(BASE, LPT, SESSION_PATH, GS)


class ConfigurationTests(unittest.TestCase):
    def test_parses_server_details_and_keeps_key_in_memory_only(self):
        http = FakeHttp([("GET", "/configuration", _resp(CONFIGURATION))])
        with patch.object(session, "_http_request", side_effect=http):
            config = session.get_configuration(BASE, GS, SESSION_PATH)

        self.assertEqual(http.calls[0]["url"], f"{BASE}{SESSION_PATH}/configuration")
        self.assertEqual(http.calls[0]["headers"]["Authorization"], f"Bearer {GS}")
        details = config.server_details
        self.assertEqual(config.keepalive_pulse_in_seconds, 30)
        self.assertEqual(details.ip_address, "10.0.0.5")
        self.assertEqual(details.port, 443)
        self.assertEqual(details.stun_server_address, "stun.example:3478")
        self.assertEqual(details.srtp_key, SRTP_KEY)
        self.assertNotIn(SRTP_KEY, repr(details))
        self.assertNotIn(SRTP_KEY, repr(config))

    def test_parses_ice_exchange_path_when_advertised(self):
        payload = {
            "keepAlivePulseInSeconds": 30,
            "serverDetails": {**SERVER_DETAILS, "iceExchangePath": "/p/adv"},
        }
        http = FakeHttp([("GET", "/configuration", _resp(payload))])
        with patch.object(session, "_http_request", side_effect=http):
            config = session.get_configuration(BASE, GS, SESSION_PATH)
        self.assertEqual(config.server_details.ice_exchange_path, "/p/adv")

    def test_missing_server_details_defaults(self):
        http = FakeHttp([("GET", "/configuration", _resp({}))])
        with patch.object(session, "_http_request", side_effect=http):
            config = session.get_configuration(BASE, GS, SESSION_PATH)
        self.assertEqual(config.server_details, session.ServerDetails())
        self.assertEqual(config.keepalive_pulse_in_seconds, 0)


class SdpExchangeTests(unittest.TestCase):
    ANSWER_WRAPPED = _resp(
        {"exchangeResponse": json.dumps(SDP_ANSWER), "errorDetails": None}
    )

    def _routes(self, *gets):
        return FakeHttp([
            ("POST", "/sdp", (202, b"", {})),
            ("GET", "/sdp", _replay(list(gets))),
        ])

    def test_offer_shape_and_answer_poll(self):
        http = self._routes(_pending(), _pending(), self.ANSWER_WRAPPED)
        sleeps = []
        with patch.object(session, "_http_request", side_effect=http):
            answer = session.exchange_sdp(
                BASE, GS, SESSION_PATH, "v=0\r\nOFFER\r\n", sleep=sleeps.append
            )

        self.assertEqual(http.urls("POST"), [f"{BASE}{SESSION_PATH}/sdp"])
        payload = http.payload(0)
        self.assertEqual(payload["messageType"], "offer")
        self.assertEqual(payload["sdp"], "v=0\r\nOFFER\r\n")
        self.assertEqual(payload["requestId"], 1)

        configuration = payload["configuration"]
        self.assertEqual(configuration["chat"], {"minVersion": 1, "maxVersion": 1})
        self.assertEqual(configuration["control"], {"minVersion": 1, "maxVersion": 3})
        self.assertEqual(configuration["input"], {"minVersion": 1, "maxVersion": 9})
        self.assertEqual(configuration["message"], {"minVersion": 1, "maxVersion": 1})
        self.assertEqual(
            configuration["reliableinput"], {"minVersion": 9, "maxVersion": 9}
        )
        self.assertEqual(
            configuration["unreliableinput"], {"minVersion": 9, "maxVersion": 9}
        )
        self.assertEqual(configuration["chatConfiguration"]["format"]["codec"], "opus")
        self.assertEqual(configuration["chatConfiguration"]["sampleFrequencyHz"], 24000)

        self.assertEqual(len(http.urls("GET")), 3)
        self.assertEqual(sleeps, [session.EXCHANGE_POLL_INTERVAL_SEC] * 2)
        self.assertEqual(
            answer,
            session.SdpAnswer(
                sdp="v=0\r\nANSWER\r\n",
                message_type="answer",
                status="success",
                chat=1,
                control=3,
                input_version=9,
                message=1,
            ),
        )

    def test_flat_answer_response_is_also_accepted(self):
        http = self._routes(_resp(SDP_ANSWER))
        with patch.object(session, "_http_request", side_effect=http):
            answer = session.exchange_sdp(
                BASE, GS, SESSION_PATH, "OFFER", sleep=_noop_sleep
            )
        self.assertEqual(answer.sdp, "v=0\r\nANSWER\r\n")

    def test_custom_configuration_block_replaces_the_default(self):
        http = self._routes(self.ANSWER_WRAPPED)
        block = {"isMediaStreamsChatRenegotiation": True}
        with patch.object(session, "_http_request", side_effect=http):
            session.exchange_sdp(
                BASE,
                GS,
                SESSION_PATH,
                "OFFER",
                configuration_block=block,
                request_id=2,
                sleep=_noop_sleep,
            )

        payload = http.payload(0)
        self.assertEqual(payload["configuration"], block)
        self.assertEqual(payload["requestId"], 2)

    def test_default_configuration_is_not_shared_between_calls(self):
        first = session.default_sdp_configuration()
        first["input"]["maxVersion"] = 99
        self.assertEqual(session.default_sdp_configuration()["input"]["maxVersion"], 9)

    def test_failure_status_raises_with_debug_info(self):
        http = self._routes(_resp({"status": "failure", "debugInfo": "sem datachannel"}))
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionError) as caught:
                session.exchange_sdp(
                    BASE, GS, SESSION_PATH, "OFFER", sleep=_noop_sleep
                )
        self.assertIn("failure", str(caught.exception))
        self.assertIn("sem datachannel", str(caught.exception))

    def test_answer_without_sdp_raises(self):
        http = self._routes(_resp({"status": "success", "sdp": ""}))
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionError):
                session.exchange_sdp(
                    BASE, GS, SESSION_PATH, "OFFER", sleep=_noop_sleep
                )

    def test_timeout_while_answer_is_pending(self):
        http = self._routes(_pending())
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionTimeoutError):
                session.exchange_sdp(
                    BASE, GS, SESSION_PATH, "OFFER", timeout=0, sleep=_noop_sleep
                )

    def test_empty_offer_is_rejected(self):
        http = FakeHttp([])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(ValueError):
                session.exchange_sdp(BASE, GS, SESSION_PATH, "")
        self.assertEqual(http.calls, [])


class IceExchangeTests(unittest.TestCase):
    LOCAL = [
        json.dumps({
            "candidate": "candidate:1 1 UDP 100 192.168.0.10 5000 typ host",
            "sdpMid": "0",
            "sdpMLineIndex": 0,
        }),
        json.dumps({
            "candidate": "candidate:2 1 UDP 100 10.0.0.2 5001 typ srflx",
            "sdpMid": "1",
            "sdpMLineIndex": 1,
        }),
    ]

    def test_post_body_is_a_json_array_of_strings(self):
        http = FakeHttp([
            ("POST", "/ice", (202, b"", {})),
            ("GET", "/ice", _resp({"candidates": ["a=candidate:remote"]})),
        ])
        with patch.object(session, "_http_request", side_effect=http):
            remote = session.exchange_ice(
                BASE, GS, f"{SESSION_PATH}/ice", self.LOCAL, sleep=_noop_sleep
            )

        raw = http.calls[0]["data"].decode("utf-8")
        self.assertEqual(http.calls[0]["url"], f"{BASE}{SESSION_PATH}/ice")
        self.assertEqual(http.calls[0]["headers"]["Authorization"], f"Bearer {GS}")
        self.assertEqual(json.loads(raw), {"candidates": self.LOCAL})
        for candidate in json.loads(raw)["candidates"]:
            self.assertIsInstance(candidate, str)
        self.assertIn("sdpMid", raw)
        self.assertIn("sdpMLineIndex", raw)
        self.assertEqual(
            remote, [session.IceCandidate(candidate="a=candidate:remote")]
        )

    def test_remote_candidates_preserve_media_line(self):
        http = FakeHttp([
            ("POST", "/ice", (202, b"", {})),
            ("GET", "/ice", _resp({"exchangeResponse": json.dumps([
                {
                    "candidate": "a=candidate:1",
                    "messageType": "iceCandidate",
                    "sdpMLineIndex": "0",
                },
                {"candidate": "a=end-of-candidates", "messageType": "iceCandidate"},
            ])})),
        ])
        with patch.object(session, "_http_request", side_effect=http):
            remote = session.exchange_ice(
                BASE, GS, f"{SESSION_PATH}/ice", self.LOCAL, sleep=_noop_sleep
            )
        self.assertEqual(
            remote,
            [
                session.IceCandidate(candidate="a=candidate:1"),
                session.IceCandidate(candidate="a=end-of-candidates"),
            ],
        )

    def test_polls_while_response_is_204(self):
        http = FakeHttp([
            ("POST", "/ice", (202, b"", {})),
            ("GET", "/ice", _replay([
                _pending(), _pending(), _resp({"candidates": []}),
            ])),
        ])
        sleeps = []
        with patch.object(session, "_http_request", side_effect=http):
            self.assertEqual(
                session.exchange_ice(
                    BASE, GS, f"{SESSION_PATH}/ice", [], sleep=sleeps.append
                ),
                [],
            )
        self.assertEqual(len(http.urls("GET")), 3)
        self.assertEqual(sleeps, [session.EXCHANGE_POLL_INTERVAL_SEC] * 2)

    def test_home_ice_exchange_path_from_configuration_is_honored(self):
        ice_path = "/v5/sessions/home/play/def/ice"
        http = FakeHttp([
            ("POST", "/ice", (202, b"", {})),
            ("GET", "/ice", _resp({"candidates": ["a=candidate:remote"]})),
        ])
        with patch.object(session, "_http_request", side_effect=http):
            session.exchange_ice(BASE, GS, ice_path, self.LOCAL, sleep=_noop_sleep)
        self.assertEqual(http.calls[0]["url"], f"{BASE}{ice_path}")

    def test_timeout_while_remote_candidates_are_pending(self):
        http = FakeHttp([
            ("POST", "/ice", (202, b"", {})),
            ("GET", "/ice", _pending()),
        ])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionTimeoutError):
                session.exchange_ice(
                    BASE,
                    GS,
                    f"{SESSION_PATH}/ice",
                    self.LOCAL,
                    timeout=0,
                    sleep=_noop_sleep,
                )

    def test_missing_ice_path_is_rejected(self):
        http = FakeHttp([])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(ValueError):
                session.exchange_ice(BASE, GS, "", self.LOCAL)
        self.assertEqual(http.calls, [])


class KeepaliveAndStopTests(unittest.TestCase):
    def test_keepalive_sends_empty_body(self):
        http = FakeHttp([("POST", "/keepalive", _resp({
            "aliveSeconds": 30, "reason": "SessionStillActive",
        }))])
        with patch.object(session, "_http_request", side_effect=http):
            pulse = session.keepalive(BASE, GS, SESSION_PATH)

        self.assertEqual(http.calls[0]["url"], f"{BASE}{SESSION_PATH}/keepalive")
        self.assertEqual(http.calls[0]["data"], b"")
        self.assertEqual(pulse, session.Keepalive(30, "SessionStillActive"))

    def test_keepalive_accepts_accepted_status_without_body(self):
        http = FakeHttp([("POST", "/keepalive", (202, b"", {}))])
        with patch.object(session, "_http_request", side_effect=http):
            self.assertEqual(
                session.keepalive(BASE, GS, SESSION_PATH), session.Keepalive()
            )

    def test_stop_deletes_the_session(self):
        http = FakeHttp([("DELETE", "/play/abc-123", (202, b"", {}))])
        with patch.object(session, "_http_request", side_effect=http):
            self.assertTrue(session.stop(BASE, GS, SESSION_PATH))

        call = http.calls[0]
        self.assertEqual(call["method"], "DELETE")
        self.assertEqual(call["url"], f"{BASE}{SESSION_PATH}")
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {GS}")
        self.assertIsNone(call["data"])

    def test_stop_failure_raises(self):
        http = FakeHttp([("DELETE", "/play/abc-123", (500, b"{}", {}))])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionHttpError):
                session.stop(BASE, GS, SESSION_PATH)


class RegionAndPlatformTests(unittest.TestCase):
    WEU = "https://weu.core.gssv-play-prod.xboxlive.com"
    EUS = "https://eus.core.gssv-play-prod.xboxlive.com"
    AUS = "https://aus.core.gssv-play-prod.xboxlive.com"

    def test_offering_maps_to_play_platform(self):
        for offering, expected in (
            ("xgpuweb", session.PLATFORM_CLOUD),
            ("xgpubeta", session.PLATFORM_CLOUD),
            ("xgpuwebf2p", session.PLATFORM_CLOUD),
            ("XHOME", session.PLATFORM_HOME),
            ("xhome", session.PLATFORM_HOME),
        ):
            self.assertEqual(session.platform_for_offering(offering), expected)

    def test_default_region_wins_over_fallback_priority(self):
        streaming = _tokens(regions=[
            {"name": "WestEurope", "baseUri": self.WEU, "fallbackPriority": 0},
            {"name": "EastUS", "baseUri": self.EUS, "isDefault": True,
             "fallbackPriority": 9},
        ])
        self.assertEqual(session.resolve_base_uri(streaming), self.EUS)

    def test_fallback_priority_order_when_no_default(self):
        streaming = _tokens(regions=[
            {"name": "A", "baseUri": self.EUS, "fallbackPriority": 30},
            {"name": "B", "baseUri": self.WEU, "fallbackPriority": 10},
            {"name": "C", "baseUri": self.AUS, "fallbackPriority": 20},
        ])
        self.assertEqual(session.resolve_base_uri(streaming), self.WEU)

    def test_regions_without_fallback_priority_keep_server_order(self):
        streaming = _tokens(regions=[
            {"name": "A", "baseUri": self.EUS},
            {"name": "B", "baseUri": self.WEU},
        ])
        self.assertEqual(session.resolve_base_uri(streaming), self.EUS)

    def test_explicit_region_name_is_case_insensitive(self):
        streaming = _tokens(regions=[
            {"name": "EastUS", "baseUri": self.EUS, "isDefault": True},
            {"name": "WestEurope", "baseUri": self.WEU},
        ])
        self.assertEqual(
            session.resolve_base_uri(streaming, "westeurope"), self.WEU
        )

    def test_unknown_region_lists_what_is_available(self):
        streaming = _tokens(regions=[
            {"name": "EastUS", "baseUri": self.EUS},
            {"name": "WestEurope", "baseUri": self.WEU},
        ])
        with self.assertRaises(session.SessionError) as caught:
            session.resolve_base_uri(streaming, "Antartida")
        self.assertIn("EastUS", str(caught.exception))
        self.assertIn("WestEurope", str(caught.exception))

    def test_login_without_regions_is_an_error(self):
        with self.assertRaises(session.SessionError):
            session.resolve_base_uri(_tokens(regions=[]))

    def test_region_rejects_non_xbox_or_credentialed_url(self):
        for base in (
            "http://eus.core.gssv-play-prod.xboxlive.com",
            "https://example.com",
            "https://user:pass@eus.core.gssv-play-prod.xboxlive.com",
        ):
            with self.subTest(base=base):
                with self.assertRaises(session.SessionError):
                    session.resolve_base_uri(
                        _tokens(
                            regions=[
                                {"name": "malicious", "baseUri": base, "isDefault": True}
                            ]
                        )
                    )

    def test_platform_for_login_uses_offering_then_override(self):
        self.assertEqual(
            session.platform_for_login(_tokens("xhome")), session.PLATFORM_HOME
        )
        self.assertEqual(
            session.platform_for_login(_tokens("xgpuweb")), session.PLATFORM_CLOUD
        )
        self.assertEqual(
            session.platform_for_login(_tokens("xhome"), session.PLATFORM_CLOUD),
            session.PLATFORM_CLOUD,
        )

    def test_ice_exchange_path_prefers_server_advertised_path(self):
        advertised = session.PreparedSession(
            base=BASE,
            platform="home",
            session_id="S",
            session_path="/p",
            state="Provisioned",
            configuration=session.SessionConfiguration(
                server_details=session.ServerDetails(ice_exchange_path="/p/adv")
            ),
        )
        derived = session.PreparedSession(
            base=BASE,
            platform="cloud",
            session_id="S",
            session_path="/p",
            state="Provisioned",
            configuration=session.SessionConfiguration(),
        )
        self.assertEqual(session.ice_exchange_path(advertised), "/p/adv")
        self.assertEqual(session.ice_exchange_path(derived), "/p/ice")


class HandshakeTests(unittest.TestCase):
    """Cadeia completa contra uma resposta falsa programada por chamada."""

    def _routes(self):
        return [
            ("POST", "/play", _resp({
                "sessionId": "SID-1",
                "sessionPath": SESSION_PATH,
                "state": "Provisioning",
            })),
            ("GET", "/state", _replay([
                _resp({"state": "Provisioning"}),
                _resp({"state": "ReadyToConnect"}),
                _resp({"state": "ReadyToConnect"}),
                _resp({"state": "Provisioned"}),
            ])),
            ("POST", "/connect", (202, b"", {})),
            ("GET", "/configuration", _resp(CONFIGURATION)),
            ("POST", "/keepalive", _resp({"aliveSeconds": 30, "reason": "Active"})),
            ("DELETE", "/play/abc-123", (202, b"", {})),
        ]

    def test_full_chain_order(self):
        http = FakeHttp(self._routes())
        with patch.object(session, "_http_request", side_effect=http):
            prepared = session.open_session(
                BASE, GS, LPT, title_id="TITLE-A", sleep=_noop_sleep
            )
            pulse = session.keepalive(BASE, GS, prepared.session_path)
            stopped = session.stop(BASE, GS, prepared.session_path)

        self.assertEqual(
            [(call["method"], call["url"][len(BASE):]) for call in http.calls],
            [
                ("POST", "/v5/sessions/cloud/play"),
                ("GET", f"{SESSION_PATH}/state"),
                ("GET", f"{SESSION_PATH}/state"),
                ("POST", f"{SESSION_PATH}/connect"),
                ("GET", f"{SESSION_PATH}/state"),
                ("GET", f"{SESSION_PATH}/state"),
                ("GET", f"{SESSION_PATH}/configuration"),
                ("POST", f"{SESSION_PATH}/keepalive"),
                ("DELETE", SESSION_PATH),
            ],
        )
        self.assertEqual(prepared.session_id, "SID-1")
        self.assertEqual(prepared.state, "Provisioned")
        self.assertEqual(prepared.platform, session.PLATFORM_CLOUD)
        self.assertEqual(prepared.base, BASE)
        self.assertEqual(prepared.configuration.server_details.srtp_key, SRTP_KEY)
        self.assertEqual(pulse.alive_seconds, 30)
        self.assertTrue(stopped)
        self.assertEqual(session.ice_exchange_path(prepared), f"{SESSION_PATH}/ice")

    def test_connect_is_sent_once_even_with_repeated_ready_to_connect(self):
        http = FakeHttp(self._routes())
        with patch.object(session, "_http_request", side_effect=http):
            session.open_session(
                BASE, GS, LPT, title_id="TITLE-A", sleep=_noop_sleep
            )
        self.assertEqual(len(http.urls("POST")), 2)  # /play e um único /connect
        self.assertEqual(
            json.loads(http.calls[3]["data"].decode()), {"userToken": LPT}
        )

    def test_open_session_for_login_resolves_region_and_platform(self):
        http = FakeHttp([
            ("POST", "/play", _resp({
                "sessionId": "SID-HOME", "sessionPath": "/v5/sessions/home/play/def",
            })),
            ("GET", "/state", _resp({"state": "Provisioned"})),
            ("GET", "/configuration", _resp({"keepAlivePulseInSeconds": 10})),
        ])
        with patch.object(session, "_http_request", side_effect=http):
            prepared = session.open_session_for_login(
                _login("xhome", [{"name": "Home", "baseUri": BASE, "isDefault": True}]),
                server_id="CONSOLE-1",
                sleep=_noop_sleep,
            )

        self.assertEqual(prepared.base, BASE)
        self.assertEqual(prepared.platform, session.PLATFORM_HOME)
        self.assertEqual(http.calls[0]["url"], f"{BASE}/v5/sessions/home/play")
        self.assertEqual(http.payload(0)["serverId"], "CONSOLE-1")
        self.assertEqual(prepared.configuration.keepalive_pulse_in_seconds, 10)


class SecretHygieneTests(unittest.TestCase):
    def test_logs_never_contain_tokens_or_srtp_key(self):
        http = FakeHttp([
            ("POST", "/play", _resp({
                "sessionId": "SID-1", "sessionPath": SESSION_PATH,
            })),
            ("GET", "/state", _resp({"state": "Provisioned"})),
            ("POST", "/sdp", (202, b"", {})),
            ("GET", "/sdp", _resp({"status": "success", "sdp": "v=0\r\nA\r\n"})),
            ("GET", "/configuration", _resp(CONFIGURATION)),
            ("POST", "/keepalive", _resp({"aliveSeconds": 30, "reason": "Active"})),
            ("DELETE", "/play/abc-123", (202, b"", {})),
        ])
        with self.assertLogs(session.logger, level="DEBUG") as captured:
            with patch.object(session, "_http_request", side_effect=http):
                prepared = session.open_session(
                    BASE, GS, LPT, title_id="TITLE-A", sleep=_noop_sleep
                )
                session.exchange_sdp(
                    BASE, GS, prepared.session_path, "v=0 OFFER", sleep=_noop_sleep
                )
                session.keepalive(BASE, GS, prepared.session_path)
                session.stop(BASE, GS, prepared.session_path)

        rendered = "\n".join(captured.output)
        for secret in (GS, LPT, SRTP_KEY):
            self.assertNotIn(secret, rendered)
        self.assertIn("SID-1", rendered)

    def test_http_errors_do_not_echo_the_bearer_token(self):
        http = FakeHttp([("GET", "/v2/titles", (401, b"unauthorized", {}))])
        with patch.object(session, "_http_request", side_effect=http):
            with self.assertRaises(session.SessionHttpError) as caught:
                session.list_titles(BASE, GS)
        self.assertNotIn(GS, str(caught.exception))

    def test_timeout_is_20_seconds_by_default(self):
        http = FakeHttp([("GET", "/v2/titles", _resp({"results": []}))])
        with patch.object(session, "_http_request", side_effect=http):
            session.list_titles(BASE, GS)
        self.assertEqual(auth.DEFAULT_TIMEOUT_SEC, 20)
        self.assertEqual(http.calls[0]["timeout"], 20)


if __name__ == "__main__":
    unittest.main()
