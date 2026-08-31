"""PHP runtime semantics, reproduced exactly.

Every function here mirrors a PHP builtin that the legacy code relies on.
Do not "fix" these to be more correct — correctness here means *identical
to PHP*, because that is the acceptance criterion.
"""
import math
import re
from typing import Any

_LEADING_INT = re.compile(r"^[+-]?\d+")
_LEADING_FLOAT = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_TAGS = re.compile(r"<[^>]*>")
_NON_ALPHA = re.compile(r"[^A-Za-z]")


def php_intval(value: Any) -> int:
    """PHP `(int)$v`.

    (int)"42"     -> 42       (int)"12abc" -> 12
    (int)"abc"    -> 0        (int)"12.9"  -> 12   (truncates toward zero)
    (int)null     -> 0        (int)true    -> 1
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)  # Python int() truncates toward zero, same as PHP
    match = _LEADING_INT.match(str(value).strip())
    return int(match.group()) if match else 0


def php_floatval(value: Any) -> float:
    """PHP `floatval($v)` / `(float)$v`. Same leading-numeric rule as intval."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    match = _LEADING_FLOAT.match(str(value).strip())
    return float(match.group()) if match else 0.0


def php_round(value: float, precision: int = 0) -> float:
    """PHP `round()` — half away from zero.

    Python's round() is banker's rounding: round(0.5) == 0, round(2.5) == 2.
    PHP:   round(0.5) == 1.0, round(2.5) == 3.0, round(-0.5) == -1.0
    Getting this wrong changes payment amounts (PR-17) and the progress
    percentage (PR-28) by one unit at every .5 boundary.
    """
    if precision == 0:
        return float(math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5))
    factor = 10.0**precision
    scaled = value * factor
    rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
    return rounded / factor


def php_round_int(value: float) -> int:
    """round() where the result is used as an integer (paise, percentages)."""
    return int(php_round(value, 0))


def php_str_pad(value: str, length: int, pad: str = " ", *, left: bool = False) -> str:
    """PHP `str_pad()`.

    Default direction is STR_PAD_RIGHT — this is the trap in PR-22:
        str_pad("Al", 3, "X")  -> "AlX"   (NOT "XAl")
    Pass left=True for STR_PAD_LEFT (used for the 6-digit id).
    """
    if length <= len(value) or not pad:
        return value
    fill = (pad * length)[: length - len(value)]
    return fill + value if left else value + fill


def php_json_is_falsy(value: Any) -> bool:
    """Emulates `if (!$data)` after json_decode (PR-26).

    Falsy in PHP: null, false, 0, 0.0, "", "0", [] (and {} which decodes
    to an empty array under assoc=true).
    Note "0" — a JSON body of literally `"0"` is rejected as invalid.
    """
    if value is None or value is False:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value in ("", "0")
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def php_clean(value: Any) -> str:
    """PHP `htmlspecialchars(strip_tags(trim($v)), ENT_QUOTES, 'UTF-8')` (PR-33).

    Order is load-bearing: trim, THEN strip tags, THEN escape. PHP does not
    re-trim after stripping, so "<b> hi</b>" -> " hi" with a leading space.
    The `&` replacement must come first or the later entities double-escape.
    """
    text = "" if value is None else str(value)
    text = _TAGS.sub("", text.strip())
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


def php_trim(value: Any) -> str:
    """PHP `trim()` with the null-coalescing default the legacy code uses."""
    return "" if value is None else str(value).strip()


def letters_only(value: str) -> str:
    """PHP `preg_replace('/[^A-Za-z]/', '', $name)` (PR-22)."""
    return _NON_ALPHA.sub("", value or "")