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
    fetch_apps,
    fetch_config,
    probe,
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


if __name__ == "__main__":
    unittest.main()
