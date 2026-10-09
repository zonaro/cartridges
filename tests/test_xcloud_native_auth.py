import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cartridges.xcloud_native import auth


def _resp(payload, code=200):
    return code, json.dumps(payload).encode("utf-8"), {}


class XCloudNativeAuthTests(unittest.TestCase):
    def test_xau_uses_ms_access_token_not_lpt(self):
        """Regressão: o RpsTicket do XAU deve levar o access_token MS, nunca o LPT."""
        seen = {}

        def fake_http(method, url, data=None, headers=None, timeout=20):
            headers = headers or {}
            if "devicecode" in url:
                return _resp(
                    {
                        "user_code": "ABCD-1234",
                        "device_code": "DEV",
                        "verification_uri": "https://microsoft.com/devicelogin",
                        "expires_in": 900,
                        "interval": 5,
                        "message": "msg",
                    }
                )
            if url.endswith("/token") and b"device_code" in (data or b""):
                return _resp(
                    {
                        "access_token": "MS-ACCESS",
                        "refresh_token": "MS-REFRESH",
                        "expires_in": 3600,
                    }
                )
            if "oauth20_token.srf" in url:
                body = dict(
                    p.split("=") for p in data.decode().split("&") if "=" in p
                )
                seen["lpt_grant_refresh"] = body.get("refresh_token")
                return _resp({"access_token": "LPT-VALUE", "expires_in": 3600})
            if "user.auth.xboxlive.com" in url:
                props = json.loads(data.decode())["Properties"]
                seen["rps_ticket"] = props["RpsTicket"]
                return _resp({"Token": "XAU-JWT"})
            if "xsts.auth.xboxlive.com" in url:
                payload = json.loads(data.decode())
                seen["xsts_rp"] = payload["RelyingParty"]
                seen["xsts_input"] = payload["Properties"]["UserTokens"][0]
                return _resp({"Token": "GSSV-JWT"})
            if "gssv-play-prod" in url:
                payload = json.loads(data.decode())
                seen.setdefault("gssv_calls", []).append(payload["offeringId"])
                seen["gssv_token"] = payload["token"]
                seen["gssv_ua"] = headers.get("User-Agent", "")
                seen["gssv_origin"] = headers.get("Origin", "")
                if payload["offeringId"] != "xhome":
                    return 401, b"{}", {}
                return _resp(
                    {
                        "gsToken": "GS",
                        "market": "pt-BR",
                        "durationInSeconds": 3600,
                        "offeringSettings": {"regions": [{"name": "r"}]},
                    }
                )
            raise AssertionError(f"URL inesperada: {url}")

        with patch.object(auth, "_http_request", side_effect=fake_http):
            with patch.object(auth, "_store_refresh_token") as store:
                result = auth.complete_device_flow(
                    auth.DeviceCodeAuth(
                        user_code="ABCD-1234",
                        device_code="DEV",
                        verification_uri="https://microsoft.com/devicelogin",
                        expires_in=900,
                        interval=5,
                        message="msg",
                    )
                )

        self.assertEqual(seen["rps_ticket"], "d=MS-ACCESS")
        self.assertEqual(seen["lpt_grant_refresh"], "MS-REFRESH")
        self.assertEqual(seen["xsts_rp"], "http://gssv.xboxlive.com/")
        self.assertEqual(seen["xsts_input"], "XAU-JWT")
        self.assertEqual(seen["gssv_token"], "GSSV-JWT")
        self.assertIn("Chrome", seen["gssv_ua"])
        self.assertNotIn("urllib", seen["gssv_ua"])
        self.assertEqual(seen["gssv_origin"], "https://www.xbox.com")
        self.assertEqual(
            seen["gssv_calls"], ["xgpuweb", "xgpuwebf2p", "xhome"]
        )
        self.assertEqual(result.lpt, "LPT-VALUE")
        self.assertEqual(result.refresh_token, "MS-REFRESH")
        self.assertEqual(result.streaming.offering, "xhome")
        self.assertEqual(result.streaming.market, "pt-BR")
        store.assert_called_once_with("MS-REFRESH")

    def test_browser_login_renews_access_when_only_refresh_given(self):
        def fake_http(method, url, data=None, headers=None, timeout=20):
            if url.endswith("/token"):
                return _resp(
                    {"access_token": "FRESH-ACCESS", "refresh_token": "FRESH-RT"}
                )
            if "oauth20_token.srf" in url:
                return _resp({"access_token": "LPT2"})
            if "user.auth.xboxlive.com" in url:
                props = json.loads(data.decode())["Properties"]
                assert props["RpsTicket"] == "d=FRESH-ACCESS"
                return _resp({"Token": "XAU2"})
            if "xsts.auth.xboxlive.com" in url:
                return _resp({"Token": "GSSV2"})
            if "gssv-play-prod" in url:
                return _resp(
                    {
                        "gsToken": "GS2",
                        "market": "en-US",
                        "durationInSeconds": 100,
                        "offeringSettings": {"regions": []},
                    }
                )
            raise AssertionError(url)

        with patch.object(auth, "_http_request", side_effect=fake_http):
            with patch.object(auth, "_store_refresh_token"):
                result = auth.login_with_browser_tokens("", refresh_token="OLD-RT")
        self.assertEqual(result.streaming.gs_token, "GS2")
        self.assertEqual(result.refresh_token, "FRESH-RT")

    def test_login_without_any_token_raises(self):
        with self.assertRaises(ValueError):
            auth.login_with_browser_tokens("", refresh_token="")

    def test_login_offering_dirigido(self):
        seen = {}

        def fake_http(method, url, data=None, headers=None, timeout=20):
            if url.endswith("/token"):
                return _resp({"access_token": "A2", "refresh_token": "R2"})
            if "oauth20_token.srf" in url:
                return _resp({"access_token": "LPT2"})
            if "user.auth.xboxlive.com" in url:
                return _resp({"Token": "XAU2"})
            if "xsts.auth.xboxlive.com" in url:
                return _resp({"Token": "GSSV2"})
            if "gssv-play-prod" in url:
                payload = json.loads(data.decode())
                seen.setdefault("calls", []).append(payload["offeringId"])
                return _resp(
                    {
                        "gsToken": "GS2",
                        "market": "pt-BR",
                        "durationInSeconds": 100,
                        "offeringSettings": {"regions": []},
                    }
                )
            raise AssertionError(url)

        with patch.object(auth, "_http_request", side_effect=fake_http):
            with patch.object(auth, "_store_refresh_token"):
                result = auth.login_offering("OLD-RT", "xgpuwebf2p")
        self.assertEqual(seen["calls"], ["xgpuwebf2p"])
        self.assertEqual(result.streaming.offering, "xgpuwebf2p")

    def test_streaming_expiry_is_parsed(self):
        before = datetime.now(timezone.utc)
        tokens = auth.StreamingTokens(
            gs_token="x", market="y", regions=[], offering="xhome",
            expires_at=before,
        )
        self.assertLessEqual(tokens.expires_at, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
