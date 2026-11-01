import json
from pathlib import Path

import streamlit as st

from coder.agent import SelfCorrectingCoder, Task
from coder.llm import make_llm

st.set_page_config(page_title="Self-Correcting Coder", layout="wide")
st.title("Self-Correcting Code Assistant")
ex = {p.stem: p for p in sorted(Path(__file__).parent.glob("examples/*.json"))}
provider = st.sidebar.selectbox("LLM provider", ["grok-cli", "openai"])
max_iters = st.sidebar.slider("Max fix iterations", 1, 8, 4)
choice = st.selectbox("Example task", ["(custom)"] + list(ex))
base = json.loads(ex[choice].read_text()) if choice in ex else {"name": "custom", "spec": "", "tests": None, "starter": None}
spec = st.text_area("Task spec", base["spec"], height=120)
tests = st.text_area("Tests (optional, read-only for the agent)", base.get("tests") or "", height=160)
starter = base.get("starter")
if starter:
    st.caption("This example starts from given (buggy) starter code.")
if st.button("Run agent") and spec:
    with st.spinner("Agent iterating (Grok CLI ~1 min per call)..."):
        res = SelfCorrectingCoder(make_llm(provider), max_iters).run(Task(base["name"], spec, tests or None, starter))
    (st.success if res.passed else st.error)(f"{'PASSED' if res.passed else 'FAILED'} after {len(res.iterations)} test run(s)")
    for it in res.iterations:
        icon = "✅" if it.test["passed"] else "❌"
        with st.expander(f"{icon} Iteration {it.iteration}: {it.action} — {it.test['summary']}", expanded=not it.test["passed"]):
            st.caption(f"sandbox: {it.test['sandbox']} | changed: {', '.join(it.changed)}")
            st.code(it.diff or "(no diff)", language="diff")
            st.code(it.test["output"][-4000:], language="text")
    st.subheader("Final files")
    for k, v in res.files.items():
        st.code(v, language="python")
