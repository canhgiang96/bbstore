"""Value coercion for uploaded Excel cells.

Port of toNumber() and parseDateValue() from ../../js/app.js. Keep in sync
with that file.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from dateutil import parser as dateutil_parser

# Excel's day-0 epoch (accounts for the historical 1900 leap-year bug), used
# only as a fallback for raw numeric date serials that weren't already
# converted to a real date by the Excel reader.
_EXCEL_EPOCH = datetime(1899, 12, 30)

_DMY_RE = re.compile(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$")
_YMD_RE = re.compile(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$")


def to_number(v) -> float:
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if v is None or v == "":
        return 0.0
    s = re.sub(r"[^\d.,-]", "", str(v))
    # A lone "0.xyz" (e.g. a Combo ratio like 0.125) is never a
    # thousands-grouped integer with a superfluous leading zero — nobody
    # writes "0.500" to mean 500 — so leave it alone. Otherwise "." followed
    # by exactly 3 digits (a full grouping) is a thousands separator to strip.
    if not re.match(r"^-?0\.\d+$", s):
        s = re.sub(r"\.(?=\d{3}(\D|$))", "", s)
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_date_value(v):
    """Returns a datetime, or None if v can't be parsed as a date."""
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return _EXCEL_EPOCH + timedelta(days=float(v))
        except (OverflowError, ValueError):
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        date_part = re.split(r"[\sT]", s)[0]

        m = _DMY_RE.match(date_part)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return datetime(y, mo, d)
            except ValueError:
                return None

        m = _YMD_RE.match(date_part)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return datetime(y, mo, d)
            except ValueError:
                return None

        try:
            # dayfirst=True matches the Vietnamese day-first convention
            # (same as _DMY_RE above) for anything that didn't match the
            # strict D/M/YYYY or YYYY/M/D regexes — e.g. dot-separated
            # dates ("05.06.2024") or a 2-digit year ("05/06/24").
            return dateutil_parser.parse(s, dayfirst=True)
        except (ValueError, OverflowError, TypeError):
            return None
    return None
