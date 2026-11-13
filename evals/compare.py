"""Side-by-side table of eval runs, e.g. Groq vs OpenRouter.

    uv run python -m evals.compare evals/reports/2026-11-13-groq.json evals/reports/2026-11-13-openrouter.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evals.run_evals import THRESHOLDS

ROWS = [
    ("role_leak_rate", "Role-leak rate (hard gate)", "pct"),
    ("approval_bypass_rate", "Approval bypass (hard gate)", "pct"),
    ("numeric_accuracy", "Numeric accuracy (KPI cases)", "pct"),
    ("tool_selection_accuracy", "Tool-selection accuracy", "pct"),
    ("rag_faithfulness", "SOP faithfulness (judge)", "pct"),
    ("judge_average", "LLM-judge average (1–5)", "num"),
    ("pass_rate", "Cases passed", "pct"),
    ("latency_p50_s", "Latency p50 (s)", "num"),
    ("latency_p95_s", "Latency p95 (s)", "num"),
    ("total_tokens", "Tokens (whole run)", "int"),
]


def fmt(value, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "int":
        return f"{value:,}"
    return f"{value:g}"


def table(paths: list[str]) -> str:
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    head = "| Metric | Threshold | " + " | ".join(
        f"{r['provider']}{' (smoke)' if r.get('smoke') else ''} · {r['metrics']['cases']} cases" for r in runs) + " |"
    lines = [head, "|---|---|" + "---|" * len(runs)]
    for key, label, kind in ROWS:
        thr = THRESHOLDS.get(key)
        shown_thr = f"{thr[0]} {fmt(thr[1], kind)}" if thr else "report"
        lines.append(f"| {label} | {shown_thr} | " + " | ".join(fmt(r["metrics"].get(key), kind) for r in runs) + " |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(table(sys.argv[1:]))
