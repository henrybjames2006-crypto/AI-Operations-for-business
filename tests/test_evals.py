from opsapp.evals.runner import run_all, write_report

SAFETY = ("approval_edit", "adapter_failure", "timeout_uncertain", "duplicate", "cross_tenant")


def test_eval_suite_runs_and_safety_scenarios_all_pass(tmp_path):
    data = run_all()
    s = data["summary"]
    assert s["cases"] >= 50
    for cat in SAFETY:
        assert s["by_category"][cat]["passed"] == s["by_category"][cat]["total"], cat
    assert s["quote_correctness"].startswith(s["quote_correctness"].split("/")[1].split()[0])
    md, js = write_report(data, tmp_path)
    assert "synthetic" in md.read_text() and js.exists()
