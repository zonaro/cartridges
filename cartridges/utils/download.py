# download.py
#
# Copyright 2024 kramo
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

"""Small helpers to read HTTP bodies into memory with a size cap.

Covers are portrait images (~600x900); even animated ones rarely exceed a few
megabytes. Capping the download keeps a misbehaving or oversized URL from
exhausting memory, and streaming means the cap is enforced even when the server
lies about (or omits) the Content-Length header. The request timeout does not
bound size: it limits silence between bytes, not the total.
"""

import threading
from typing import Any

import requests

# 25 MiB is generous for any cover, animated included, while still bounding the
# worst case.
MAX_IMAGE_BYTES = 25 * 1024 * 1024

# For API answers (JSON, HTML pages). 10 MiB covers with room to spare the
# largest real one — a HowLongToBeat /game/<id> page, ~1–2 MiB.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

_local = threading.local()


class ResponseTooLargeError(requests.RequestException):
    """Raised when a body exceeds the allowed size.

    Subclasses ``requests.RequestException`` so existing download error handling
    (which already catches request failures) treats an oversized body the same
    way as any other failed request.
    """


def _get(url: str, **kwargs: Any) -> requests.Response:
    """``requests.get`` through this thread's own session, keeping connections.

    ``requests.get`` opens a new connection (DNS, TCP, TLS) on every call. A
    picker downloads up to 30 previews in a row, and a fresh connection is what
    pays the 1-7 s stalls of a lost packet with no RTT estimate yet (measured on
    28/09/2026). One session per thread, not one global: ``requests.Session``
    is not documented as thread-safe, and the wallpaper picker downloads with
    six workers at once.
    """
    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = requests.Session()
    try:
        return session.get(url, **kwargs)
    finally:
        # `requests.get` never carried a cookie from one call to the next, and
        # a Steam cookie (country, session) must not change a later answer.
        session.cookies.clear()


def read_capped(response: requests.Response, max_bytes: int) -> bytes:
    """Read a streamed body, raising :class:`ResponseTooLargeError` past
    ``max_bytes``. The request must have been made with ``stream=True``, or the
    whole body is already in memory before this runs."""
    buffer = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise ResponseTooLargeError(f"body exceeded {max_bytes} bytes")
    return bytes(buffer)


def get_capped(
    url: str, max_bytes: int = MAX_RESPONSE_BYTES, **kwargs: Any
) -> requests.Response:
    """A GET whose body is read under a cap before it is returned.

    A drop-in for call sites that go on to use ``.json()`` / ``.status_code``:
    the body is already read, so ``.json()`` parses what was read and never
    reads more. Raises :class:`ResponseTooLargeError` past ``max_bytes``.
    """
    response = _get(url, stream=True, **kwargs)
    try:
        body = read_capped(response, max_bytes)
    except BaseException:
        response.close()
        raise
    # ponytail: sets requests' own cache of the body (what `.content` fills on
    # first access), private but stable for a decade; a wrapper type would be
    # the upgrade if requests ever moves it.
    response._content = body  # pylint: disable=protected-access
    return response


def download_bytes(
    url: str, timeout: float = 10, max_bytes: int = MAX_IMAGE_BYTES
) -> bytes:
    """Download ``url`` into memory, aborting if it exceeds ``max_bytes``.

    :raises requests.HTTPError: on a 4xx/5xx response
    :raises ResponseTooLargeError: if the payload exceeds ``max_bytes``
    """
    with _get(url, timeout=timeout, stream=True) as response:
        response.raise_for_status()

        # Trust a declared length when it's clearly too big (fail fast), but
        # always enforce the cap while streaming since the header can be wrong.
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    raise ResponseTooLargeError(url)
            except ValueError:
                pass

        return read_capped(response, max_bytes)
