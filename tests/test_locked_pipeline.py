"""Immutable supplied tests, followup test generation, sandbox bounds, and CLI integration."""
import json
import subprocess
from unittest.mock import patch

import pytest

from coder import cli
from coder.agent import SelfCorrectingCoder, Task, unified_diff
from coder.llm import MockLLM
from coder.sandbox import run_pytest, safe_path


def outcome(passed: bool) -> dict:
    return {
        "passed": passed,
        "output": "done",
        "failures": [] if passed else ["test_solution.py::test_value"],
        "summary": "passed" if passed else "failed",
        "sandbox": "test double",
    }


def test_fix_cannot_replace_supplied_tests():
    locked = "def test_value():\n    assert True\n"
    initial = "=== FILE: solution.py ===\nvalue = 1\n=== END ===\n"
    fix = (
        "=== FILE: solution.py ===\nvalue = 2\n=== END ===\n"
        "=== FILE: test_solution.py ===\npass\n=== END ===\n"
    )
    with patch("coder.agent.run_pytest", side_effect=[outcome(False), outcome(True)]) as runner:
        result = SelfCorrectingCoder(MockLLM([initial, fix]), max_iters=1).run(
            Task("x", "implement value", tests=locked)
        )
    assert result.passed
    assert result.files["test_solution.py"] == locked
    assert result.files["solution.py"] == "value = 2\n"
    assert runner.call_count == 2
    assert result.iterations[1].changed == ["solution.py"]


def test_zero_fix_budget_runs_starter_exactly_once():
    with patch("coder.agent.run_pytest", return_value=outcome(False)) as runner:
        result = SelfCorrectingCoder(MockLLM([]), max_iters=0).run(
            Task("x", "x", tests="assert False\n", starter="value = 0\n")
        )
    assert not result.passed
    assert len(result.iterations) == runner.call_count == 1


def test_missing_generated_tests_triggers_followup_prompt_and_format_retry():
    llm = MockLLM(
        [
            "no file blocks on first try",
            "=== FILE: solution.py ===\ndef f(): return 7\n=== END ===\n",
            "=== FILE: test_solution.py ===\nfrom solution import f\ndef test_f(): assert f() == 7\n=== END ===\n",
        ]
    )
    with patch("coder.agent.run_pytest", return_value=outcome(True)):
        result = SelfCorrectingCoder(llm, max_iters=1).run(Task("seven", "return 7", tests=None))
    assert result.passed
    assert "solution.py" in result.files and "test_solution.py" in result.files
    assert len(llm.prompts) == 3
    assert "Only return test_solution.py." in llm.prompts[2]


def test_diff_handles_deleted_and_added_modules():
    diff = unified_diff({"old.py": "a\n"}, {"new.py": "b\n"})
    assert "--- a/old.py" in diff and "-a" in diff
    assert "+++ b/new.py" in diff and "+b" in diff


@pytest.mark.parametrize("bad_rel", ["../escape.py", "/abs.py", "sub/../../out.py"])
def test_sandbox_rejects_unsafe_relative_paths(tmp_path, bad_rel):
    with pytest.raises(ValueError, match="unsafe path"):
        safe_path(tmp_path, bad_rel)


def test_sandbox_timeout_remains_a_failed_result():
    with (
        patch("coder.sandbox.netns_available", return_value=False),
        patch("coder.sandbox.subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 1)),
    ):
        result = run_pytest({"test_x.py": "assert True\n"}, timeout=1)
    assert not result["passed"]
    assert result["returncode"] == -9
    assert "TIMEOUT after 1s" in result["output"]


def test_cli_adhoc_spec_writes_out_dir_and_emits_brief_json(tmp_path, capsys):
    out_dir = tmp_path / "exported"
    mock_llm = MockLLM(
        [
            "=== FILE: solution.py ===\ndef inc(x): return x + 1\n=== END ===\n"
            "=== FILE: test_solution.py ===\nfrom solution import inc\ndef test_i(): assert inc(1) == 2\n=== END ===\n"
        ]
    )
    with (
        patch("coder.cli.make_llm", return_value=mock_llm),
        patch("coder.agent.run_pytest", return_value=outcome(True)),
    ):
        cli.main(["--spec", "Write inc(x)", "--max-iters", "1", "--out", str(out_dir), "--brief"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["task"] == "adhoc"
    assert (out_dir / "solution.py").read_text(encoding="utf-8") == "def inc(x): return x + 1\n"


def test_conftest_hook_injection_blocked_and_all_skipped_rejected():
    supplied_tests = "from solution import solve\ndef test_solve():\n    assert solve() == 42\n"
    conftest_cheat = (
        "=== FILE: solution.py ===\ndef solve(): return 0\n=== END ===\n"
        "=== FILE: conftest.py ===\n"
        "def pytest_collection_modifyitems(items):\n    items.clear()\n"
        "=== END ===\n"
        "=== FILE: sub/conftest.py ===\npass\n=== END ===\n"
        "=== FILE: pytest.ini ===\n[pytest]\npython_files = none_*.py\n=== END ===\n"
    )
    res = SelfCorrectingCoder(MockLLM([conftest_cheat]), max_iters=0).run(
        Task("lock-check", "return 42", tests=supplied_tests)
    )
    assert not res.passed
    assert "conftest.py" not in res.files
    assert "sub/conftest.py" not in res.files
    assert "pytest.ini" not in res.files

    skip_res = run_pytest({"test_skip.py": "import pytest\ndef test_s():\n    pytest.skip('bypass')\n"})
    assert skip_res["returncode"] == 0
    assert skip_res["passed"] is False
