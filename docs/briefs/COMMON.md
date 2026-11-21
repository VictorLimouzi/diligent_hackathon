# Common ground for every stream

Casebook decides sanctions-screening hits (`DIFFERENT_ENTITY`, `SAME_ENTITY`, `REVIEW`) with a deterministic rule engine in `casebook/`. You are adding an advisory layer around it. Read `README.md` first, then `contracts/`.

Hard rules:

1. **Never change a decision.** `casebook/`, `run.py`, `eval.py` and `tests/test_casebook.py` are off limits. The scored output must stay byte-identical; `tests/test_casebook.py::Golden` checks it.
2. **Stay inside your directory.** Create and edit files only where your brief says. If you need a change elsewhere (a contract field, an engine helper), do not make it: write the request in `NOTES.md` inside your directory.
3. **Contracts are frozen.** `contracts/types.ts`, `contracts/python_interfaces.md` and `contracts/fixtures/` define what you consume and produce. Match them exactly.
4. **Do not commit, push, or switch branches.** Leave your work in the working tree; the integrator commits it.
5. Policy text for the domain is at `data/screening/dev/POLICY.md`.

Environment:

- Dev data (not in git): `data/screening/dev` — pass it as the data folder, or set `CASEBOOK_DATA`.
- Python with all dependencies: `.venv/bin/python` (scikit-learn, FastAPI, anthropic, pytest). The engine itself is standard library only.
- All data is invented; none of it is personal data.

When you finish, write `NOTES.md` in your directory: what you built, how to run and test it, what you verified and how, and anything left undone or uncertain. State failures plainly.
