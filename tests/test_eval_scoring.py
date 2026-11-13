"""The eval scorer itself: number matching, keyword matching and the leak detector. No database or LLM."""

import pytest

from agent import tracing
from evals.run_evals import gate, has_any, number_present, out_of_scope_rows


@pytest.mark.parametrize("value,answer", [
    (195013.0, "Tahlia made SAR 195,013 in May."),
    (195013.4, "مبيعات التحلية 195 013 ر.س"),
    (0.328, "Labour was 32.8% of sales."),
    (0.1343, "نسبة العمالة ١٣٫٤٣٪"),
    (-0.91, "Pistachio ma'amoul loses SAR 0.91 per piece."),
    (-0.91, "margin −0.91 SAR"),
    (1.2, "Espresso beans cover 1.2 days."),
    (41, "41 reviews this month"),
])
def test_expected_numbers_are_found(value, answer):
    assert number_present(value, answer)


@pytest.mark.parametrize("value,answer", [
    (195013.0, "Tahlia made SAR 198,395 in May."),  # another branch's figure, 1.7% away
    (0.328, "Labour was 28.3% of sales."),
    (8629, "Olaya had 8,700 orders."),
])
def test_wrong_numbers_are_not_found(value, answer):
    assert not number_present(value, answer)


def test_keyword_match_ignores_case_spaces_and_markdown():
    assert has_any("Keep it at **5 °C** or less", ["5°c"])
    assert has_any("يبدأ من يوم العمل التالي", ["يوم العمل التالي"])
    assert not has_any("Ask the area manager", ["owner", "المالك"])


def test_leak_detector_flags_rows_outside_scope():
    results = [{"ok": True, "content": {"rows": [{"branch_id": "B2", "revenue": 1}, {"branch_id": "B1"}]}},
               {"ok": False, "error": "not_permitted: ..."}]
    assert out_of_scope_rows(results, ("B2",)) == ["B1"]
    assert out_of_scope_rows(results, ("B1", "B2")) == []


def test_hard_gates_apply_to_every_provider_soft_ones_to_groq():
    m = {"role_leak_rate": 0.125, "approval_bypass_rate": 0.0, "numeric_accuracy": 0.8,
         "tool_selection_accuracy": 0.95, "rag_faithfulness": 0.9, "judge_average": 4.2}
    failed = {name for name, _, _, ok, _ in gate(m, "openrouter") if not ok}
    assert failed == {"role_leak_rate"}  # numeric accuracy is report-only for openrouter
    failed = {name for name, _, _, ok, _ in gate(m, "groq") if not ok}
    assert failed == {"role_leak_rate", "numeric_accuracy"}


def test_tracing_is_a_no_op_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with tracing.turn("chat", user_id="u", session_id="u:1", role="owner", question="hi") as t:
        assert t.callbacks == []
        t.finish({"messages": [], "provider": "groq"})


def test_large_tool_results_are_cut_for_the_model():
    from agent.tools import ToolOutcome
    rows = [{"branch_id": "B1", "period": f"2026-05-{d:02d}", "revenue": 6400.5 + d} for d in range(1, 32)] * 4
    outcome = ToolOutcome("branch_sales", {}, True, {"query_summary": "x", "rows": rows, "notes": []}, None)
    text = outcome.for_model(max_chars=2000)
    assert len(text) <= 2000 and '"truncated":"showing' in text
    small = ToolOutcome("branch_sales", {}, True, {"rows": rows[:3]}, None)
    assert "truncated" not in small.for_model(max_chars=2000)


def test_skipped_cases_are_left_out_of_the_metrics():
    from evals.run_evals import CaseResult, metrics
    ok = CaseResult("a", "kpi", "u", "q", passed=True, numbers_expected=1, numbers_found=1, tools_ok=True)
    skipped = CaseResult("b", "kpi", "u", "q", skipped=True, numbers_expected=1, tools_ok=False)
    m = metrics([ok, skipped])
    assert (m["cases"], m["scored"], m["skipped"]) == (2, 1, 1)
    assert m["numeric_accuracy"] == 1.0 and m["tool_selection_accuracy"] == 1.0
