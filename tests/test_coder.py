from pathlib import Path

from coder.agent import SelfCorrectingCoder, Task, unified_diff
from coder.llm import MockLLM, parse_files
from coder.sandbox import run_pytest

TESTS = "from solution import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
BUGGY = "=== FILE: solution.py ===\ndef add(a, b):\n    return a - b\n=== END ==="
FIXED = "=== FILE: solution.py ===\ndef add(a, b):\n    return a + b\n=== END ==="


def test_parse_files_strips_fences():
    f = parse_files("x\n=== FILE: a.py ===\n```python\nprint(1)\n```\n=== END ===\n")
    assert f == {"a.py": "print(1)\n"}


def test_sandbox_pass_fail_timeout():
    assert run_pytest({"solution.py": "def add(a,b): return a+b\n", "test_solution.py": TESTS})["passed"]
    r = run_pytest({"solution.py": "def add(a,b): return 0\n", "test_solution.py": TESTS})
    assert not r["passed"] and r["failures"] == ["test_solution.py::test_add"]
    r = run_pytest({"test_x.py": "import time\ndef test_s():\n    time.sleep(10)\n"}, timeout=2)
    assert not r["passed"] and "TIMEOUT" in r["output"]


def test_sandbox_blocks_network():
    r = run_pytest({"test_net.py": "import socket\ndef test_n():\n    socket.create_connection(('1.1.1.1', 53), timeout=2)\n"})
    if r["sandbox"] == "netns+tmpdir":
        assert not r["passed"]


def test_fail_then_fix():
    llm = MockLLM([BUGGY, FIXED])
    res = SelfCorrectingCoder(llm, max_iters=3).run(Task("add", "add two numbers", TESTS))
    assert res.passed and len(res.iterations) == 2
    assert "-    return a - b" in res.iterations[1].diff and "+    return a + b" in res.iterations[1].diff
    assert "test_solution.py::test_add" in llm.prompts[1]  # failure fed back


def test_locked_tests_not_overwritten_and_max_iters():
    cheat = BUGGY + "\n=== FILE: test_solution.py ===\ndef test_add():\n    pass\n=== END ==="
    res = SelfCorrectingCoder(MockLLM([BUGGY, cheat, cheat]), max_iters=2).run(Task("add", "add", TESTS))
    assert not res.passed and len(res.iterations) == 3 and res.files["test_solution.py"] == TESTS


def test_starter_and_generated_tests():
    gen = "=== FILE: test_solution.py ===\n" + TESTS + "=== END ==="
    res = SelfCorrectingCoder(MockLLM([gen, FIXED])).run(Task("add", "add", None, "def add(a, b):\n    return 0\n"))
    assert res.passed and res.iterations[0].action.startswith("starter")


def test_examples_load():
    ex = list((Path(__file__).parent.parent / "examples").glob("*.json"))
    assert len(ex) == 3 and all(Task.load(p).spec for p in ex)


def test_diff():
    assert "+x" in unified_diff({}, {"a": "x\n"})
