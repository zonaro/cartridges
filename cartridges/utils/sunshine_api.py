# sunshine_api.py
#
# Copyright 2026 Jolven contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only Sunshine Web UI client (status and app list).

Sunshine (lizardbyte/sunshine) serves its Web UI over HTTPS with a
self-signed certificate and guards every ``/api/*`` route with HTTP
Basic auth against the username/password created in the Web UI. There
is no login endpoint and no session cookie: each request carries the
``Authorization`` header. Requests without an ``Origin``/``Referer``
header (i.e. non-browser clients like this one) are exempt from the
CSRF check, so no token handling is needed.

Credentials passed here live only in memory: this module never
persists or logs them. Timeouts apply to every request and Sunshine
being stopped is reported as :class:`SunshineApiError`, never raised
as a connection traceback to the UI.
"""

from __future__ import annotations

import logging
import urllib3

from dataclasses import dataclass, field
from typing import Any

import requests
from requests.auth import HTTPBasicAuth
from requests.exceptions import RequestException

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 47990
TIMEOUT = 10


class SunshineApiError(Exception):
    """Raised when Sunshine cannot be reached or rejects the request."""


@dataclass(frozen=True)
class SunshineConnection:
    """Where to reach Sunshine and how to authenticate (memory only)."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    username: str = ""
    password: str = ""

    @property
    def base_url(self) -> str:
        return f"https://{(self.host or DEFAULT_HOST).strip() or DEFAULT_HOST}:{self.port}"


@dataclass
class SunshineStatus:
    """What the dedicated Sunshine page shows after a successful probe."""

    version: str = ""
    platform: str = ""
    apps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def app_names(self) -> list[str]:
        names = []
        for app in self.apps:
            name = app.get("name") if isinstance(app, dict) else None
            if isinstance(name, str) and name.strip():
                names.append(name)
        return names


def _request(
    connection: SunshineConnection, method: str, path: str
) -> requests.Response:
    if not 1 <= connection.port <= 65535:
        raise SunshineApiError("invalid Sunshine port")
    auth = (
        HTTPBasicAuth(connection.username, connection.password)
        if connection.username or connection.password
        else None
    )
    try:
        response = requests.request(
            method,
            connection.base_url + path,
            auth=auth,
            verify=False,
            timeout=TIMEOUT,
        )
    except RequestException as error:
        raise SunshineApiError("Sunshine is not reachable") from error
    if response.status_code == 401:
        raise SunshineApiError("Sunshine rejected the credentials")
    if response.status_code in (301, 302, 303, 307, 308) and "welcome" in (
        response.headers.get("location", "")
    ):
        raise SunshineApiError("Sunshine has no user configured yet")
    try:
        response.raise_for_status()
    except RequestException as error:
        raise SunshineApiError(
            f"Sunshine answered {response.status_code}"
        ) from error
    return response


def _as_json(response: requests.Response, what: str) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise SunshineApiError(f"unexpected {what} from Sunshine") from error


def fetch_apps(connection: SunshineConnection) -> list[dict[str, Any]]:
    """Return the Sunshine app list (each entry keeps its ``name``)."""
    payload = _as_json(_request(connection, "GET", "/api/apps"), "app list")
    if not isinstance(payload, dict):
        raise SunshineApiError("unexpected app list from Sunshine")
    apps = payload.get("apps", [])
    if not isinstance(apps, list):
        raise SunshineApiError("unexpected app list from Sunshine")
    logging.debug("Sunshine returned %d apps", len(apps))
    return [app for app in apps if isinstance(app, dict)]


def fetch_config(connection: SunshineConnection) -> dict[str, str]:
    """Return the Sunshine host config as plain string pairs."""
    payload = _as_json(_request(connection, "GET", "/api/config"), "config")
    if not isinstance(payload, dict):
        raise SunshineApiError("unexpected config from Sunshine")
    return {str(k): str(v) for k, v in payload.items()}


def probe(connection: SunshineConnection) -> SunshineStatus:
    """Check the connection and summarize Sunshine for the settings page."""
    config = fetch_config(connection)
    apps = fetch_apps(connection)
    return SunshineStatus(
        version=config.get("version", ""),
        platform=config.get("platform", ""),
        apps=apps,
    )
