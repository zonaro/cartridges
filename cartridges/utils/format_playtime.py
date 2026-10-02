# format_playtime.py
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


def format_playtime(seconds: int) -> str:
    """Human-readable playtime, capped at hours (never days/weeks).

    Under an hour it shows whole minutes ("45 min"); from an hour on it shows
    fractional hours with a comma decimal ("10,5 horas").
    """
    if seconds < 60:
        return _("menos de 1 min")
    minutes = round(seconds / 60)
    if minutes < 60:
        return _("{} min").format(minutes)

    hours = round(seconds / 3600, 1)
    if hours == 1:
        return _("1 hora")
    text = str(int(hours)) if hours.is_integer() else str(hours).replace(".", ",")
    return _("{} horas").format(text)


def format_stopwatch(seconds: int) -> str:
    """The running clock shown while a game is open: ``0:05:12``.

    Unlike :func:`format_playtime`, which rounds because nobody cares whether a
    library says 10,5 or 10,6 hours, this one is watched second by second, so it
    shows every unit and never rounds. Hours are not padded and not capped: they
    are a counter, not a field, and a session that reaches 100 hours should say
    so rather than wrap.
    """
    seconds = max(0, seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02}"


if __name__ == "__main__":
    import builtins

    builtins._ = lambda s: s  # type: ignore  # stand in for gettext

    assert format_playtime(0) == "menos de 1 min"
    assert format_playtime(59) == "menos de 1 min"
    assert format_playtime(60) == "1 min"
    assert format_playtime(1800) == "30 min"
    assert format_playtime(3599) == "1 hora"  # rounds up to a full hour, not "60 min"
    assert format_playtime(3600) == "1 hora"
    assert format_playtime(5400) == "1,5 horas"
    assert format_playtime(37800) == "10,5 horas"
    assert format_playtime(90000) == "25 horas"  # stays in hours, never days

    assert format_stopwatch(0) == "0:00:00"
    assert format_stopwatch(-1) == "0:00:00"  # never a negative clock
    assert format_stopwatch(9) == "0:00:09"
    assert format_stopwatch(312) == "0:05:12"
    assert format_stopwatch(3600) == "1:00:00"
    assert format_stopwatch(45296) == "12:34:56"
    assert format_stopwatch(360000) == "100:00:00"  # hours are never capped
    print("ok")
