import base64
import hashlib
import json
import unittest
import urllib.parse
from unittest.mock import patch

from cartridges.xcloud_login import (
    REDIRECT_URI,
    TOKEN_URL,
    LoginFlow,
    build_authorize_url,
    exchange_code,
    make_pkce,
    parse_redirect_tokens,
)
from cartridges.xcloud_native import auth


class FakeAuth:
    MSAL_CLIENT_ID = "fake-client"

    def __init__(self, falha_complete=False):
        self.falha_complete = falha_complete

    def begin_device_flow(self):
        return {
            "user_code": "ABCD-1234",
            "device_code": "DEV",
            "verification_uri": "https://microsoft.com/link",
            "expires_in": 900,
            "interval": 5,
            "message": "instr",
        }

    def complete_device_flow(self, device):
        if self.falha_complete:
            raise Exception("Falha simulada")
        return "RESULT-DEVICE"


class XCloudLoginTests(unittest.TestCase):
    def test_authorize_url_has_pkce_params(self):
        url = build_authorize_url(REDIRECT_URI, "STATE", "CHALLENGE")
        parsed = urllib.parse.urlsplit(url)
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertIn("login.microsoftonline.com", parsed.netloc)
        self.assertEqual(qs["client_id"], [auth.MSAL_CLIENT_ID])
        self.assertEqual(qs["response_type"], ["code"])
        self.assertEqual(qs["response_mode"], ["fragment"])
        self.assertEqual(qs["redirect_uri"], [REDIRECT_URI])
        self.assertEqual(qs["state"], ["STATE"])
        self.assertEqual(qs["code_challenge"], ["CHALLENGE"])
        self.assertEqual(qs["code_challenge_method"], ["S256"])
        self.assertIn("xboxlive.signin", qs["scope"][0])

    def test_make_pkce_challenge_matches_verifier(self):
        verifier, challenge, state = make_pkce()
        self.assertTrue(verifier)
        self.assertTrue(state)
        expect = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expect)

    def test_parse_redirect_fragment_code(self):
        tokens = parse_redirect_tokens(
            REDIRECT_URI + "#code=ABC&state=S", expected_state="S"
        )
        self.assertEqual(tokens["code"], "ABC")
        self.assertEqual(tokens["state"], "S")

    def test_parse_redirect_query_code(self):
        tokens = parse_redirect_tokens(
            REDIRECT_URI.split("?")[0] + "?code=ABC&state=S", expected_state="S"
        )
        self.assertEqual(tokens["code"], "ABC")

    def test_parse_redirect_state_mismatch_raises(self):
        with self.assertRaises(ValueError):
            parse_redirect_tokens(REDIRECT_URI + "#code=ABC&state=WRONG", expected_state="S")

    def test_parse_redirect_error_param_raises(self):
        with self.assertRaises(ValueError):
            parse_redirect_tokens(REDIRECT_URI + "#error=access_denied&error_description=nope")

    def test_parse_redirect_fragment_access_tokens(self):
        tokens = parse_redirect_tokens(
            REDIRECT_URI + "#access_token=AT&refresh_token=RT&state=S", expected_state="S"
        )
        self.assertEqual(tokens["access_token"], "AT")
        self.assertEqual(tokens["refresh_token"], "RT")

    def test_device_flow_success_sync(self):
        estagios = []
        flow = LoginFlow(
            auth_module=FakeAuth(),
            on_stage=lambda f, s, d: estagios.append((s, d.get("result"))),
            blocking=True,
        )
        flow.start_device()
        self.assertEqual([s for s, _ in estagios], ["working", "device", "done"])
        self.assertEqual({s: r for s, r in estagios}.get("done"), "RESULT-DEVICE")

    def test_device_flow_error_then_retry(self):
        estagios = []
        auth_mod = FakeAuth(falha_complete=True)
        flow = LoginFlow(
            auth_module=auth_mod,
            on_stage=lambda f, s, d: estagios.append((s, d.get("message"))),
            blocking=True,
        )
        flow.start_device()
        self.assertEqual(estagios[-1][0], "error")
        self.assertIn("Falha simulada", estagios[-1][1])
        self.assertEqual(flow.last_mode, "device")

        auth_mod.falha_complete = False
        estagios.clear()
        flow.start_device()
        self.assertEqual([s for s, _ in estagios], ["working", "device", "done"])

    def test_browser_flow_exchanges_and_logs_in(self):
        estagios = []
        flow = LoginFlow(
            auth_module=auth,
            on_stage=lambda f, s, d: estagios.append((s, d.get("result"))),
            blocking=True,
        )
        url = flow.start_browser()
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
        redirect = REDIRECT_URI + f"#code=CODE&state={state}"

        body = json.dumps({"access_token": "AT", "refresh_token": "RT"}).encode("utf-8")

        def fake_http(method, target, data=None, headers=None, timeout=20):
            self.assertEqual(method, "POST")
            self.assertEqual(target, TOKEN_URL)
            form = urllib.parse.parse_qs(data.decode())
            self.assertEqual(form["grant_type"], ["authorization_code"])
            self.assertEqual(form["code"], ["CODE"])
            self.assertIn("code_verifier", form)
            self.assertEqual(form["redirect_uri"], [REDIRECT_URI])
            return 200, body, {}

        with patch.object(auth, "_http_request", side_effect=fake_http):
            with patch.object(auth, "login_with_browser_tokens", return_value="LOGIN-OK") as login:
                consumido = flow.handle_redirect(redirect)

        self.assertTrue(consumido)
        self.assertEqual({s: r for s, r in estagios}.get("done"), "LOGIN-OK")
        login.assert_called_once_with("AT", "RT")

    def test_browser_flow_wrong_state_emits_error(self):
        estagios = []
        flow = LoginFlow(
            auth_module=auth,
            on_stage=lambda f, s, d: estagios.append((s, d.get("message"))),
            blocking=True,
        )
        flow.start_browser()
        consumido = flow.handle_redirect(REDIRECT_URI + "#code=CODE&state=WRONG")
        self.assertTrue(consumido)
        self.assertEqual(estagios[-1][0], "error")
        self.assertIn("state", estagios[-1][1])

    def test_handle_redirect_foreign_uri_returns_false(self):
        flow = LoginFlow(auth_module=FakeAuth(), blocking=True)
        self.assertFalse(flow.handle_redirect("https://www.example.com/outro"))

    def test_exchange_code_uses_pkce_verifier(self):
        body = json.dumps({"access_token": "AT", "refresh_token": "RT"}).encode("utf-8")
        seen = {}

        def fake_http(method, url, data=None, headers=None, timeout=20):
            seen["url"] = url
            seen["form"] = urllib.parse.parse_qs(data.decode())
            return 200, body, {}

        with patch.object(auth, "_http_request", side_effect=fake_http):
            access, refresh = exchange_code("CODE", REDIRECT_URI, "VERIFIER")

        self.assertEqual((access, refresh), ("AT", "RT"))
        self.assertEqual(seen["url"], TOKEN_URL)
        self.assertEqual(seen["form"]["code_verifier"], ["VERIFIER"])
        self.assertEqual(seen["form"]["client_id"], [auth.MSAL_CLIENT_ID])


if __name__ == "__main__":
    unittest.main()