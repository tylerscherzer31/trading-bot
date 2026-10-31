---
description: Pick up the next unchecked Phase task and do it on a branch with a PR
---

1. Read `CLAUDE.md` (current phase) and the matching phase row in `SPEC.md` §9.
2. Read the "Next Phase 0 tasks" checklist in `README.md` and pick the first unchecked item. If $ARGUMENTS names a task, do that one instead.
3. Before writing code, state in 3–5 bullets what you'll build, which SPEC sections govern it, and anything you need from Tyler (keys, numbers, approvals). Stop and ask if a trading rule or number is unclear.
4. Work on a new branch `feat/<short-name>`. `main` is protected; never commit to it directly.
5. Add tests for all new logic. Risk, fill, or data-alignment logic needs edge-case tests.
6. Run `uv run ruff check .` and `uv run pytest` until both pass.
7. Commit, then ask Tyler before `git push`. After pushing, give him the compare URL to open the PR.
8. Tick the item in README's checklist in the same PR.
