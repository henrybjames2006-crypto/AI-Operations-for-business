from opsapp.ai.mock import MockExtractor
from opsapp.evals.runner import (
    SCENARIO_CATEGORIES,
    run_all,
    run_compare,
    write_compare_report,
    write_report,
)


def test_eval_suite_runs_and_safety_scenarios_all_pass(tmp_path):
    data = run_all()
    s = data["summary"]
    assert s["cases"] >= 90
    for cat in SCENARIO_CATEGORIES:
        assert s["by_category"][cat]["passed"] == s["by_category"][cat]["total"], cat
    # The rule-based reader was built with the original cases; they must keep passing.
    assert s["reading"]["original"]["cases_fully_correct"].startswith("40/40")
    # Held-out results are reported, whatever they are.
    assert s["reading"]["heldout"]["cases"] == 30
    md, js = write_report(data, tmp_path)
    assert "held-out" in md.read_text() and js.exists()


def test_compare_report_runs_without_network(tmp_path):
    data = run_compare(MockExtractor, "mock-as-ai")
    assert set(data["readers"]) == {"mock/deterministic-rules-v1", "mock-as-ai"}
    assert data["disagreements"] == []  # same reader on both sides
    md, _ = write_compare_report(data, tmp_path)
    text = md.read_text()
    assert "Held-out cases: fully correct" in text and "(none)" in text
