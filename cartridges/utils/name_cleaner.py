# name_cleaner.py
#
# Copyright 2024 redclaw
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

"""Utilities to normalize raw game names coming from shortcuts/file names.

Shortcut file names frequently carry technical clutter such as
``Game (DX12)``, ``Game - Windows``, ``Game™`` or ``Game - Shortcut`` that
hurts both readability and online cover/metadata lookups. ``clean_game_name``
strips that noise conservatively, never returning an empty string.
"""

import re

# Standalone tokens that are pure noise in a game title.
_NOISE_TOKENS = {
    "windows",
    "win32",
    "win64",
    "dx9",
    "dx10",
    "dx11",
    "dx12",
    "directx",
    "directx9",
    "directx10",
    "directx11",
    "directx12",
    "vulkan",
    "opengl",
    "x64",
    "x86",
    "32bit",
    "64bit",
}

# Noise only when bracketed: "Hades (PC)" is the game Hades, but "PC Building
# Simulator" is a title in its own right. Requiring the brackets keeps the
# stripping safe for words that are common enough to appear in a real name.
_BRACKET_NOISE_TOKENS = _NOISE_TOKENS | {
    "pc",
    "steam",
    "epic",
    "gog",
    "uwp",
    "origin",
    "battlenet",
}

_TRADEMARK_RE = re.compile("[™®©]")  # ™ ® ©
_BRACKETS_RE = re.compile(r"\s*[\(\[\{][^)\]}]*[\)\]\}]")  # (...), [...], {...}
_SHORTCUT_SUFFIX_RE = re.compile(
    r"\s*[-–—]\s*"
    r"(shortcut|atalho|acceso\s+directo|raccourci|verkn[üu]pfung)\s*$",
    re.IGNORECASE,
)
_LAUNCH_PREFIX_RE = re.compile(
    r"^\s*(play|launch|start|jogar|iniciar)\s+", re.IGNORECASE
)
_DIRECTX_PHRASE_RE = re.compile(r"\bdirectx\s*1?[0-2]\b", re.IGNORECASE)
_WS_RE = re.compile(r"\s{2,}")
_SEARCH_SEPARATORS_RE = re.compile(r"[-–—:;,./]+")
# Typographic apostrophes, folded onto the plain ASCII one for queries. Stores
# index the straight form: HowLongToBeat found "Assassin's Creed Mirage" and
# missed "Assassin’s Creed Shadows" purely because the shortcut carried a
# curly quote, and the search term never matched anything.
_APOSTROPHE_RE = re.compile("[’ʼ‘‛´`]")


def _normalize(token: str) -> str:
    """Reduce a token to its alphanumeric core for noise comparison."""
    return re.sub(r"[^a-z0-9]", "", token.lower())


def _is_noise(token: str) -> bool:
    return _normalize(token) in _NOISE_TOKENS


def _strip_noise_brackets(match: re.Match) -> str:
    """Drop bracketed groups whose content is purely a noise token.

    An empty group counts too: "(DirectX 12)" is emptied by the phrase rule
    before this runs, and "ICARUS ( )" reached the library.
    """
    inner = _normalize(match.group(0))
    return " " if not inner or inner in _BRACKET_NOISE_TOKENS else match.group(0)


def clean_game_name(name: str) -> str:
    """Return a tidied version of ``name`` for display and online lookups."""
    if not name:
        return name

    cleaned = name.strip()

    # Defensive: drop a trailing shortcut extension if one slipped through.
    for ext in (".lnk", ".url"):
        if cleaned.lower().endswith(ext):
            cleaned = cleaned[: -len(ext)]

    # Underscores commonly stand in for spaces in dumped file names.
    cleaned = cleaned.replace("_", " ")

    cleaned = _TRADEMARK_RE.sub("", cleaned)
    cleaned = _SHORTCUT_SUFFIX_RE.sub("", cleaned)
    cleaned = _LAUNCH_PREFIX_RE.sub("", cleaned)
    cleaned = _DIRECTX_PHRASE_RE.sub(" ", cleaned)
    cleaned = _BRACKETS_RE.sub(_strip_noise_brackets, cleaned)

    # Drop standalone noise tokens, but keep at least one token.
    tokens = cleaned.split()
    kept = [token for token in tokens if not _is_noise(token)]
    cleaned = " ".join(kept if kept else tokens)

    cleaned = _WS_RE.sub(" ", cleaned).strip(" -–—")
    return cleaned or name.strip()


def clean_for_search(name: str) -> str:
    """``clean_game_name`` variant tuned for online title searches.

    Also replaces separators (dashes, colons, dots, …) with spaces and folds
    curly apostrophes onto the ASCII one. Steam and SteamGridDB often miss a
    title like "Black Myth - Wukong" that matches fine as "Black Myth Wukong",
    and a shortcut spelled "Assassin’s Creed Shadows" finds nothing at all in
    catalogues that store "Assassin's". Only used to build the query, never for
    display — the game keeps the punctuation it came with on screen.
    """
    display = clean_game_name(name)
    cleaned = _SEARCH_SEPARATORS_RE.sub(" ", _APOSTROPHE_RE.sub("'", display))
    return re.sub(r"\s+", " ", cleaned).strip() or display
