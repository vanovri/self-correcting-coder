"""Write code -> run tests in sandbox -> parse failures -> fix -> repeat."""
from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .llm import FORMAT_HELP, parse_files
from .sandbox import run_pytest

WRITE_PROMPT = """You are a senior Python engineer. Implement the task below in a module named solution.py.
{tests_part}
Task:
{spec}

{fmt}"""

TESTS_GIVEN = "Tests are provided (test_solution.py, read-only); make them pass:\n{tests}\n"
TESTS_NEEDED = ("No tests were given: ALSO write test_solution.py with focused pytest tests "
                "(normal cases, edge cases, errors) that import from solution.\n")

FIX_PROMPT = """Your code fails its tests. Fix it.
Task:
{spec}

Current files:
{files}

Pytest output (sandboxed run):
{output}

Failing tests: {failures}
Rules: return ONLY the files you change, in full. Read-only files you must NOT return: {locked}.
{fmt}"""


@dataclass
class Task:
    name: str
    spec: str
    tests: str | None = None
    starter: str | None = None  # optional initial (possibly buggy) solution.py

    @classmethod
    def load(cls, path: str | Path) -> "Task":
        return cls(**json.loads(Path(path).read_text()))


@dataclass
class Iteration:
    iteration: int
    action: str
    changed: list[str]
    diff: str
    test: dict


@dataclass
class Result:
    task: str
    passed: bool
    iterations: list[Iteration] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def unified_diff(old: dict, new: dict) -> str:
    out = []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name, ""), new.get(name, "")
        if a != b:
            out += difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{name}", f"b/{name}")
    return "".join(out)


class SelfCorrectingCoder:
    def __init__(self, llm, max_iters: int = 4, timeout: int = 60, on_step=None):
        self.llm, self.max_iters, self.timeout, self.on_step = llm, max_iters, timeout, on_step

    def _gen(self, prompt: str, allowed=None) -> dict:
        files = parse_files(self.llm.complete(prompt))
        if not files:  # one retry with a format reminder
            files = parse_files(self.llm.complete(prompt + "\n\nYour last reply had no file blocks. " + FORMAT_HELP))
        return {k: v for k, v in files.items() if allowed is None or k not in allowed}

    def run(self, task: Task) -> Result:
        locked = {"test_solution.py"} if task.tests else set()
        files: dict[str, str] = {}
        if task.starter:
            files["solution.py"], action = task.starter, "starter code (given)"
        else:
            tp = TESTS_GIVEN.format(tests=task.tests) if task.tests else TESTS_NEEDED
            files.update(self._gen(WRITE_PROMPT.format(tests_part=tp, spec=task.spec, fmt=FORMAT_HELP), locked))
            action = "write code" + ("" if task.tests else " + tests")
        if task.tests:
            files["test_solution.py"] = task.tests
        elif "test_solution.py" not in files:
            files.update(self._gen(WRITE_PROMPT.format(tests_part=TESTS_NEEDED, spec=task.spec, fmt=FORMAT_HELP)
                                   + "\nOnly return test_solution.py."))
        res, prev = Result(task.name, False), {}
        for i in range(self.max_iters + 1):
            t = run_pytest(files, timeout=self.timeout)
            it = Iteration(i, action, sorted(k for k in files if files[k] != prev.get(k)), unified_diff(prev, files), t)
            res.iterations.append(it)
            if self.on_step:
                self.on_step(it)
            if t["passed"] or i == self.max_iters:
                res.passed = t["passed"]
                break
            fix = self._gen(FIX_PROMPT.format(
                spec=task.spec, files="\n".join(f"=== FILE: {k} ===\n{v}=== END ===" for k, v in files.items()),
                output=t["output"][-6000:], failures=", ".join(t["failures"]) or "see output",
                locked=", ".join(sorted(locked)) or "none", fmt=FORMAT_HELP), locked)
            prev, files, action = dict(files), {**files, **fix}, f"fix ({len(t['failures'])} failing)"
        res.files = files
        return res
