import argparse
import json
from pathlib import Path

from .agent import SelfCorrectingCoder, Task
from .llm import make_llm


def main(argv=None):
    ap = argparse.ArgumentParser(prog="self-correct")
    ap.add_argument("task", nargs="?", help="path to example JSON task")
    ap.add_argument("--spec", help="free-text task spec (instead of a JSON file)")
    ap.add_argument("--tests", type=Path, help="optional pytest file for --spec")
    ap.add_argument("--max-iters", type=int, default=4)
    ap.add_argument("--provider", choices=["openai", "grok-cli"])
    ap.add_argument("--out", type=Path, help="write final files into this dir")
    ap.add_argument("--brief", action="store_true", help="compact JSON summary")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.spec:
        task = Task("adhoc", a.spec, a.tests.read_text() if a.tests else None)
    elif a.task:
        task = Task.load(a.task)
    else:
        ap.error("give a task file or --spec")
    show = None if (a.brief or a.json) else (lambda it: print(
        f"\n### iteration {it.iteration}: {it.action} | changed {it.changed} | {it.test['summary']} [{it.test['sandbox']}]\n"
        + (it.diff[:3000] or "(no diff)") + ("" if it.test["passed"] else "\n--- test output (tail) ---\n" + it.test["output"][-1500:])))
    res = SelfCorrectingCoder(make_llm(a.provider), a.max_iters, on_step=show).run(task)
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        for k, v in res.files.items():
            (a.out / k).write_text(v)
    if a.json:
        print(json.dumps(res.to_dict(), indent=1))
    elif a.brief:
        print(json.dumps({"task": res.task, "passed": res.passed, "iterations": [
            {"i": it.iteration, "action": it.action, "summary": it.test["summary"], "failures": it.test["failures"]}
            for it in res.iterations], "solution.py": res.files.get("solution.py", "")}, indent=1))
    else:
        print(f"\nRESULT: {'PASS' if res.passed else 'FAIL'} after {len(res.iterations)} test run(s)")


if __name__ == "__main__":
    main()
