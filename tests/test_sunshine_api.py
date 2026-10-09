# test_sunshine_api.py
#
# Copyright 2026 Jolven contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the Sunshine read-only API client (no network)."""

import unittest

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from requests.exceptions import ConnectionError

from cartridges.utils.sunshine_api import (
    SunshineApiError,
    SunshineConnection,
    approve_pairing,
    cancel_pairing,
    create_app,
    delete_app,
    fetch_apps,
    fetch_config,
    get_clients,
    get_pending_pairings,
    probe,
    resolve_app_index,
    restart,
    unpair_all_clients,
    unpair_client,
    update_config,
    upload_cover,
)


def make_response(
    status: int = 200,
    payload: Any = None,
    headers: dict | None = None,
    broken_json: bool = False,
) -> SimpleNamespace:
    def json() -> Any:
        if broken_json:
            raise ValueError("nope")
        return payload

    def raise_for_status() -> None:
        if status >= 400:
            raise FakeHTTPError(status)

    return SimpleNamespace(
        status_code=status,
        headers=headers or {},
        json=json,
        raise_for_status=raise_for_status,
    )


class FakeHTTPError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.response = SimpleNamespace(status_code=status)


class SunshineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = SunshineConnection(
            host="localhost", port=47990, username="admin", password="secret"
        )

    def test_base_url_defaults(self) -> None:
        self.assertEqual(
            SunshineConnection().base_url, "https://localhost:47990"
        )

    def test_unreachable_becomes_api_error(self) -> None:
        with patch(
            "cartridges.utils.sunshine_api.requests.request",
            side_effect=ConnectionError("down"),
        ):
            with self.assertRaises(SunshineApiError):
                fetch_apps(self.connection)

    def test_wrong_credentials(self) -> None:
        with patch(
            "cartridges.utils.sunshine_api.requests.request",
            return_value=make_response(401),
        ):
            with self.assertRaisesRegex(SunshineApiError, "credentials"):
                fetch_apps(self.connection)

    def test_welcome_redirect_means_no_user(self) -> None:
        with patch(
            "cartridges.utils.sunshine_api.requests.request",
            return_value=make_response(302, headers={"location": "/welcome"}),
        ):
            with self.assertRaisesRegex(SunshineApiError, "no user"):
                fetch_apps(self.connection)

    def test_apps_uses_basic_auth_without_origin(self) -> None:
        seen: dict = {}

        def fake_request(
            method: str, url: str, **kwargs: Any
        ) -> SimpleNamespace:
            seen.update(method=method, url=url, kwargs=kwargs)
            return make_response(payload={"env": {}, "apps": [{"name": "Doom"}]})

        with patch(
            "cartridges.utils.sunshine_api.requests.request",
            side_effect=fake_request,
        ):
            apps = fetch_apps(self.connection)
        self.assertEqual([a["name"] for a in apps], ["Doom"])
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(seen["url"], "https://localhost:47990/api/apps")
        self.assertNotIn("Origin", seen["kwargs"].get("headers", {}) or {})
        self.assertFalse(seen["kwargs"].get("verify", True))
        self.assertEqual(seen["kwargs"].get("timeout"), 10)

    def test_probe_summarizes_config_and_apps(self) -> None:
        def fake_request(
            method: str, url: str, **kwargs: Any
        ) -> SimpleNamespace:
            if url.endswith("/api/config"):
                return make_response(
                    payload={"version": "9.9", "platform": "linux"}
                )
            return make_response(payload={"apps": [{"name": "A"}, {}]})

        with patch(
            "cartridges.utils.sunshine_api.requests.request",
            side_effect=fake_request,
        ):
            status = probe(self.connection)
        self.assertEqual(status.version, "9.9")
        self.assertEqual(status.platform, "linux")
        self.assertEqual(status.app_names, ["A"])

    def test_malformed_payloads_raise(self) -> None:
        with patch(
            "cartridges.utils.sunshine_api.requests.request",
            return_value=make_response(payload=["not", "a", "dict"]),
        ):
            with self.assertRaises(SunshineApiError):
                fetch_apps(self.connection)
        with patch(
            "cartridges.utils.sunshine_api.requests.request",
            return_value=make_response(broken_json=True),
        ):
            with self.assertRaises(SunshineApiError):
                fetch_config(self.connection)

    def test_invalid_port_rejected_before_network(self) -> None:
        bad = SunshineConnection(port=0)
        with patch(
            "cartridges.utils.sunshine_api.requests.request",
            side_effect=AssertionError("must not reach network"),
        ):
            with self.assertRaises(SunshineApiError):
                fetch_apps(bad)


class SunshineWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = SunshineConnection(
            host="localhost", port=47990, username="admin", password="secret"
        )
        self.calls: list = []

    def fake(self, payload: Any = None, status: int = 200) -> Any:
        def fake_request(
            method: str, url: str, **kwargs: Any
        ) -> SimpleNamespace:
            if callable(payload):
                body = payload(method, url, kwargs.get("json"))
            else:
                body = payload
            self.calls.append((method, url, kwargs.get("json")))
            return make_response(status=status, payload=body)

        return patch(
            "cartridges.utils.sunshine_api.requests.request",
            side_effect=fake_request,
        )

    def test_update_config_merges_then_posts_whole(self) -> None:
        def routes(_method: str, url: str, _body: Any) -> Any:
            if url.endswith("/api/config") and _method == "GET":
                return {"port": "47989", "version": "9.9"}
            return {"ok": True}

        with self.fake(routes):
            merged = update_config(self.connection, {"max_bitrate": "20000"})
        self.assertEqual(merged["max_bitrate"], "20000")
        self.assertEqual(merged["port"], "47989")
        method, url, body = self.calls[-1]
        self.assertEqual((method, url), ("POST", "https://localhost:47990/api/config"))
        self.assertEqual(body["max_bitrate"], "20000")

    def test_restart_posts(self) -> None:
        with self.fake({}):
            restart(self.connection)
        self.assertEqual(
            (self.calls[-1][0], self.calls[-1][1]),
            ("POST", "https://localhost:47990/api/restart"),
        )

    def test_delete_resolves_index_by_name(self) -> None:
        def routes(method: str, url: str, _body: Any) -> Any:
            if url.endswith("/api/apps") and method == "GET":
                return {"apps": [{"name": "B"}, {"name": "A"}]}
            return {}

        with self.fake(routes):
            self.assertEqual(resolve_app_index(self.connection, "A"), 1)
            delete_app(self.connection, "A")
        method, url, _body = self.calls[-1]
        self.assertEqual(method, "DELETE")
        self.assertTrue(url.endswith("/api/apps/1"))

    def test_delete_missing_is_silent(self) -> None:
        with self.fake({"apps": []}):
            delete_app(self.connection, "Missing")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[-1][0], "GET")

    def test_create_app_forces_new_index(self) -> None:
        with self.fake({"status": True}):
            create_app(self.connection, {"name": "Doom", "cmd": "doom"})
        _method, _url, body = self.calls[-1]
        self.assertEqual(body["index"], -1)
        self.assertEqual(body["name"], "Doom")
        with self.assertRaises(SunshineApiError):
            create_app(self.connection, {"name": "", "cmd": "doom"})

    def test_upload_cover_returns_server_path(self) -> None:
        with self.fake({"status": True, "path": "/cfg/covers/jolven_1.png"}):
            path = upload_cover(self.connection, "jolven 1!", b"\x89PNG")
        self.assertEqual(path, "/cfg/covers/jolven_1.png")
        _method, _url, body = self.calls[-1]
        self.assertEqual(body["key"], "jolven1")
        with self.assertRaises(SunshineApiError):
            upload_cover(self.connection, "!!!", b"\x89PNG")
        with self.assertRaises(SunshineApiError):
            upload_cover(self.connection, "k", b"")

    def test_pairings_flow(self) -> None:
        pair = {"id": "ab12", "name": "TV", "address": "1.2.3.4"}
        with self.fake({"pairings": [pair, "junk"]}):
            self.assertEqual(get_pending_pairings(self.connection), [pair])
        with self.fake({"status": True}):
            approve_pairing(self.connection, "ab12", "1234", "TV")
        _method, _url, body = self.calls[-1]
        self.assertEqual(
            body, {"pairing_id": "ab12", "pin": "1234", "name": "TV"}
        )
        with self.fake({"status": True}):
            cancel_pairing(self.connection, "ab12")
        _method, url, body = self.calls[-1]
        self.assertTrue(url.endswith("/api/pin"))
        self.assertEqual(body, {"pairing_id": "ab12"})
        with self.assertRaises(SunshineApiError):
            approve_pairing(self.connection, "ab12", "", "TV")

    def test_clients_flow(self) -> None:
        client = {"uuid": "u1", "name": "Phone"}
        with self.fake({"named_certs": [client]}):
            self.assertEqual(get_clients(self.connection), [client])
        with self.fake({"status": True}):
            unpair_client(self.connection, "u1")
        _method, url, body = self.calls[-1]
        self.assertTrue(url.endswith("/api/clients/unpair"))
        self.assertEqual(body, {"uuid": "u1"})
        with self.fake({"status": True}):
            unpair_all_clients(self.connection)
        self.assertTrue(self.calls[-1][1].endswith("/api/clients/unpair-all"))


if __name__ == "__main__":
    unittest.main()
