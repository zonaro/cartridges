# title_match.py
#
# Copyright 2026 joaomgabaldi
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

"""Compare a local game title against a store title.

Matching titles by string equality is too brittle: the same game shows up as
``Game``, ``Game™``, ``Game®`` or ``Game: Definitive Edition``, and sequel
numbers alternate between arabic and roman digits (``Civilization VI`` /
``Civilization 6``). But loosening the comparison until those all match also
makes ``The Outer Worlds`` match ``The Outer Worlds 2``, which is a *different
game* — the exact failure this module exists to prevent.

The way out is to treat the two kinds of difference separately instead of
running everything through one similarity score:

1. **Normalization** flattens the noise. Trademark signs, punctuation, case,
   accents and article words disappear, and every numeral is rewritten to a
   canonical integer, so ``VI`` and ``6`` become the same token.
2. **The numeric signature is a hard gate.** The ordered list of numbers in a
   title is its identity: ``[]`` for ``The Outer Worlds`` and ``[2]`` for
   ``The Outer Worlds 2``. Differing signatures are rejected outright, before
   any scoring, so no amount of textual similarity can ever collapse a game
   into its own sequel.
3. **The remaining words are scored.** Extra words that are only edition
   fluff (``Definitive Edition``, ``GOTY``) still identify the same game;
   extra words naming separate content (``Soundtrack``, a DLC's subtitle) do
   not.
"""

import re
import unicodedata
from dataclasses import dataclass

# Scores returned by compare_titles, from certain to useless.
SCORE_EXACT = 100  # same game, same edition
SCORE_EDITION = 90  # same game, a repackaged edition of it
SCORE_RELATED = 40  # same franchise/base title, but a distinct product
SCORE_MISMATCH = 0  # not the same game

# The lowest score that may be adopted without asking the user. Kept as a
# constant so callers agree on where "certain enough" sits.
CONFIDENT_SCORE = SCORE_EDITION

# Words carrying no identity of their own. Dropped from both sides so
# "Alien: Isolation" and "Alien Isolation" compare equal, and a stray article
# in a shortcut name ("Witcher 3" vs "The Witcher 3") is not a difference.
_STOPWORDS = frozenset({"the", "a", "an", "of", "and"})

# Extra words that mark a repackaging of the same game. A candidate that only
# adds these is still the game we are looking for.
_EDITION_WORDS = frozenset(
    {
        "edition",
        "editions",
        "goty",
        "year",
        "game",
        "definitive",
        "complete",
        "collection",
        "deluxe",
        "ultimate",
        "premium",
        "anniversary",
        "enhanced",
        "extended",
        "remastered",
        "remaster",
        "hd",
        "4k",
        "redux",
        "director",
        "directors",
        "cut",
        "standard",
        "gold",
        "platinum",
        "legacy",
        "classic",
        "original",
        "special",
        "digital",
        "full",
        "version",
        "pc",
        "steam",
    }
)

# Extra words that mark a *different product* sold under the same title:
# soundtracks, DLC, demos, tools. Their presence disqualifies a candidate even
# when everything else lines up, because buying/reading them gives the wrong
# metadata entirely (a soundtrack has no Metacritic score, a demo has no
# reviews).
_DISQUALIFYING_WORDS = frozenset(
    {
        "soundtrack",
        "soundtracks",
        "ost",
        "score",
        "album",
        "dlc",
        "expansion",
        "season",
        "pass",
        "pack",
        "bundle",
        "upgrade",
        "demo",
        "beta",
        "playtest",
        "prologue",
        "trial",
        "artbook",
        "art",
        "book",
        "comic",
        "wallpaper",
        "avatar",
        "avatars",
        "emote",
        "sdk",
        "editor",
        "server",
        "dedicated",
        "tool",
        "tools",
        "benchmark",
        "vr",
    }
)

# Roman numerals worth recognizing. Deliberately a whitelist rather than a
# general parser: tokens like "MIX" and "DIC" are valid roman numerals but are
# far more likely to be words, and mis-reading one as a number would invent a
# numeric signature that blocks a correct match.
_ROMAN_NUMERALS = {
    "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
    "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
    "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19, "xx": 20,
}  # fmt: skip

# Spelled-out numbers. "one" is left out on purpose: it is a pronoun far more
# often than a sequel number ("No One Lives Forever"), and reading it as a
# digit would give that title a signature no store entry can match.
_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip

_TRADEMARK_RE = re.compile("[™®©]")  # ™ ® ©
_APOSTROPHE_RE = re.compile("['’ʼ´`]")
# Separa em tudo que não é caractere de palavra (unicode) — e no underscore,
# que é \w mas serve de separador em nomes vindos de arquivo. A classe antiga,
# [^a-z0-9], apagava qualquer script não-latino: "ペルソナ" tokenizava para
# lista vazia (o metadado nunca resolvia, em silêncio) e "ペルソナ5" para
# ["5"], que passava o portão numérico e casava como "exato" com qualquer
# outro título japonês que também carregasse um 5 — a rota real são os aliases
# do HowLongToBeat. Kana, kanji e cirílico agora são tokens como quaisquer
# outros; a comparação continua a mesma.
_NON_ALNUM_RE = re.compile(r"[\W_]+")

# A possessive author credit opening the title: "Sid Meier's Civilization VI",
# "Tom Clancy's The Division". Stores carry it, shortcuts usually don't.
_POSSESSIVE_PREFIX_RE = re.compile(r"^[^:\-–—]{1,30}['’]s\s")

# How many leading words may be branding ("Disney Epic Mickey", "LEGO Star
# Wars") before the extras stop looking like a publisher tag and start looking
# like a different game.
_MAX_BRANDING_PREFIX_WORDS = 2


def _strip_accents(text: str) -> str:
    """Fold accented characters onto their ASCII base (``Pokémon`` → ``Pokemon``)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def tokenize(title: str) -> list[str]:
    """Split ``title`` into comparable tokens with every numeral canonicalized.

    Apostrophes are deleted rather than replaced by a space so ``Spacer's``
    becomes one token (``spacers``) instead of two.
    """
    text = _strip_accents(_TRADEMARK_RE.sub("", title or ""))
    text = _APOSTROPHE_RE.sub("", text).casefold()
    text = text.replace("&", " and ")
    tokens = [token for token in _NON_ALNUM_RE.split(text) if token]

    canonical = []
    for index, token in enumerate(tokens):
        is_last = index == len(tokens) - 1
        if token.isdigit():
            # Strip leading zeros so "07" and "7" agree, keeping "0" itself.
            canonical.append(str(int(token)))
        elif token in _ROMAN_NUMERALS:
            canonical.append(str(_ROMAN_NUMERALS[token]))
        elif token in _NUMBER_WORDS:
            canonical.append(str(_NUMBER_WORDS[token]))
        elif token == "i" and is_last and index > 0:
            # A lone "I" is only a numeral when it closes a longer title
            # ("The Last of Us Part I"); anywhere else it is the pronoun.
            canonical.append("1")
        else:
            canonical.append(token)
    return canonical


def numeric_signature(tokens: list[str]) -> list[str]:
    """Return the ordered numeric tokens — the part that identifies a sequel.

    Order is preserved because position carries meaning: ``Left 4 Dead`` is
    ``["4"]`` while ``Left 4 Dead 2`` is ``["4", "2"]``.
    """
    return [token for token in tokens if token.isdigit()]


def core_words(tokens: list[str]) -> list[str]:
    """Return the meaningful non-numeric words of a title."""
    return [
        token
        for token in tokens
        if not token.isdigit() and token not in _STOPWORDS
    ]


@dataclass(frozen=True)
class TitleMatch:
    """Outcome of comparing a wanted title against a store title."""

    score: int
    reason: str

    @property
    def confident(self) -> bool:
        """True when the match may be adopted without asking the user."""
        return self.score >= CONFIDENT_SCORE


def compare_titles(wanted: str, candidate: str) -> TitleMatch:
    """Score how well ``candidate`` identifies the same game as ``wanted``.

    :return: a :class:`TitleMatch`; ``score`` is one of the ``SCORE_*``
        constants and ``reason`` explains the verdict for the logs.
    """
    wanted_tokens = tokenize(wanted)
    candidate_tokens = tokenize(candidate)

    if not wanted_tokens or not candidate_tokens:
        return TitleMatch(SCORE_MISMATCH, "empty title")

    # The sequel gate. Checked first and on its own: a difference here means a
    # different game no matter how similar the words are.
    wanted_numbers = numeric_signature(wanted_tokens)
    candidate_numbers = numeric_signature(candidate_tokens)
    if wanted_numbers != candidate_numbers:
        return TitleMatch(
            SCORE_MISMATCH,
            f"numeric signature {candidate_numbers} != {wanted_numbers}",
        )

    wanted_core = core_words(wanted_tokens)
    candidate_core = core_words(candidate_tokens)

    if wanted_core == candidate_core:
        return TitleMatch(SCORE_EXACT, "titles match")

    # A title can tokenize to nothing but numbers and stopwords — "7", "300",
    # "1917", "The" — and by this point the numeric gate has already accepted
    # the candidate, so everything below would be reasoning about words that
    # are not there: an empty set is a subset of anything, and looking for the
    # first wanted word inside the candidate has nothing to look for. There is
    # no honest verdict available, and saying so is better than inventing one:
    # "7" is not "7 Days to Die".
    if not wanted_core:
        return TitleMatch(SCORE_MISMATCH, "no core words")

    # Everything the wanted title says must be present, or the candidate is
    # simply another game that happens to share some words.
    wanted_set = set(wanted_core)
    candidate_set = set(candidate_core)
    if not wanted_set.issubset(candidate_set):
        missing = sorted(wanted_set - candidate_set)
        return TitleMatch(SCORE_MISMATCH, f"missing words {missing}")

    extras = [word for word in candidate_core if word not in wanted_set]

    # A soundtrack or demo shares the game's whole title, so only this list
    # tells them apart.
    if disqualifying := [w for w in extras if w in _DISQUALIFYING_WORDS]:
        return TitleMatch(SCORE_MISMATCH, f"separate product: {disqualifying}")

    # Where the extra words sit matters. Words *before* the title are a
    # publisher or author credit the store adds and shortcuts drop; words
    # *after* it usually name the edition — or a whole separate product.
    # The search always finds one: `wanted_set` is non-empty (guarded above)
    # and every one of its words is in the candidate (checked just above).
    first_wanted = next(
        index for index, word in enumerate(candidate_core) if word in wanted_set
    )
    prefix_extras = candidate_core[:first_wanted]
    suffix_extras = [
        word for word in candidate_core[first_wanted:] if word not in wanted_set
    ]

    if prefix_extras and not _is_branding_prefix(
        prefix_extras, candidate, len(wanted_core)
    ):
        return TitleMatch(SCORE_RELATED, f"unexpected prefix: {prefix_extras}")

    if all(word in _EDITION_WORDS for word in suffix_extras):
        return TitleMatch(SCORE_EDITION, f"edition of the same game: {extras}")

    # Extra words that name content ("Trauma", "Spacer's Choice") mean a
    # distinct product. Reported rather than accepted, so a caller can offer it
    # as a suggestion instead of adopting it silently.
    return TitleMatch(SCORE_RELATED, f"related title: {suffix_extras}")


def _is_branding_prefix(
    prefix_extras: list[str], raw_candidate: str, wanted_word_count: int
) -> bool:
    """True when leading extra words look like a publisher/author credit.

    A possessive credit is unambiguous. Otherwise a short prefix is only
    forgiven when the wanted title is specific enough on its own that a couple
    of leading words cannot be what distinguishes two different games.
    """
    if _POSSESSIVE_PREFIX_RE.match(raw_candidate):
        return True
    return (
        len(prefix_extras) <= _MAX_BRANDING_PREFIX_WORDS and wanted_word_count >= 2
    )


def rank_candidates(
    wanted: str,
    candidates: list[dict],
    name_key: str = "name",
) -> list[tuple[dict, TitleMatch]]:
    """Sort store results best-match-first, dropping outright mismatches.

    Ties are broken by signals that distinguish a base game from its
    re-releases: a Metacritic score and a lower appid both point at the
    original entry, which is the one carrying the reviews users expect.
    """
    scored = []
    for candidate in candidates:
        match = compare_titles(wanted, str(candidate.get(name_key, "")))
        if match.score > SCORE_MISMATCH:
            scored.append((candidate, match))

    def sort_key(entry: tuple[dict, TitleMatch]) -> tuple:
        candidate, match = entry
        has_metascore = bool(str(candidate.get("metascore") or "").strip())
        try:
            appid = int(candidate.get("id") or candidate.get("appid") or 0)
        except (TypeError, ValueError):
            appid = 0
        return (-match.score, not has_metascore, appid)

    return sorted(scored, key=sort_key)
