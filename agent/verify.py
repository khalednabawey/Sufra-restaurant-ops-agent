"""The number check: every figure in an answer must come from a tool result.

A number in the draft passes if it matches a number in this turn's tool results (or the user's
question) after allowing for display rounding, thousands separators, and a ratio shown as a
percentage. Dates, years, branch ids and small counts (0–31) are not checked: they appear in
the question, the period and the prose far more often than as computed figures. A small number
written as a percentage ("3%") is checked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬٪", "0123456789.,%")
GROUP_SPACE = re.compile(r"(?<=\d)[    ](?=\d{3}(?!\d))")  # "195 013" -> "195,013"
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
BRANCH = re.compile(r"\bB[1-4]\b", re.IGNORECASE)
ACTION_REF = re.compile(r"#\s?\d+")
NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")


def numbers_in_text(text: str) -> list[tuple[str, float]]:
    text = GROUP_SPACE.sub(",", text.translate(ARABIC_DIGITS))
    for pattern in (DATE, BRANCH, ACTION_REF):
        text = pattern.sub(" ", text)
    found = []
    for m in NUMBER.finditer(text):
        raw = m.group(0)
        value = float(raw.replace(",", ""))
        is_pct = text[m.end():m.end() + 2].lstrip().startswith("%")
        if value.is_integer() and (2020 <= value <= 2030 or (0 <= value <= 31 and not is_pct)):
            continue
        found.append((raw, value))
    return found


def numbers_in_results(results: Iterable[Any]) -> set[float]:
    out: set[float] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.add(float(x))
        elif isinstance(x, str):
            out.update(v for _, v in numbers_in_text(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    for r in results:
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except ValueError:
                pass
        walk(r)
    return out


def _decimals(raw: str) -> int:
    return len(raw.split(".")[1]) if "." in raw else 0


def matches(raw: str, value: float, known: set[float]) -> bool:
    tol_display = 0.5 * 10 ** -_decimals(raw)  # 12,346 matches 12345.67; 31.2 matches 31.24
    for k in known:
        for candidate in (k, k * 100, k / 100):
            if abs(candidate - value) <= max(tol_display, 0.005 * abs(candidate)):
                return True
            if abs(abs(candidate) - abs(value)) <= tol_display:  # "a 12% drop" for -12
                return True
    return False


def unverified_numbers(answer: str, results: Iterable[Any], question: str = "",
                       known_text: str = "") -> list[str]:
    """Figures in `answer` that no tool result, the question or `known_text` (e.g. the policy facts
    in the system prompt) supports."""
    known = numbers_in_results(results) | {v for _, v in numbers_in_text(f"{question}\n{known_text}")}
    bad = []
    for raw, value in numbers_in_text(answer):
        if not matches(raw, value, known) and raw not in bad:
            bad.append(raw)
    return bad
