---
name: precommit
description: Pre-commit gate. Reviews the current diff for correctness and cleanup, runs a security pass, then runs the checks relevant to what changed (backend pytest and/or frontend type-check) before preparing a commit. Invoke before committing changes.
---

# Pre-commit gate

Run the steps below in order. After each step, give a one-line summary of what
happened. If a step needs a human decision — a likely behavior change, a real
security finding, or a failing test — STOP and surface it rather than pushing
ahead. Do not create the commit until I confirm.

Optional argument: `$ARGUMENTS` is a focus hint passed to the review steps
(e.g. `/precommit watch the inheritance merge logic`).

## 1. Show what changed

Inventory the changes with `git status` and `git diff` (include `git diff
--staged` for staged changes). Confirm the change set reads as a single coherent
commit — flag stray files, leftover debug output, print statements, or edits
unrelated to the task. Note **which areas changed** — paths under `backend/`
vs `frontend/` — to decide which checks below to run.

## 2. Correctness + cleanup review

Run `/code-review --fix` over the diff. Apply correctness fixes and readability
cleanups (duplication, deeply nested conditionals, redundant code). For any
change that alters behavior rather than just clarity, do NOT auto-apply — list
it and ask me first.

## 3. Security pass

Run `/security-review` on the changes. Report findings with severity. This is
static analysis and will not catch runtime-only issues (e.g. authorization
flaws that only appear when the app is running), so treat it as one layer.
Offer to fix high/medium findings; leave low or theoretical ones noted.

## 4. Run backend tests (if `backend/` changed)

Skip this step if the diff touched no `backend/` files. Otherwise run the suite
— pytest is a dev-only dependency not in the runtime image, so install it first:

```bash
docker compose exec -T backend sh -c "pip install -q -r requirements-dev.txt && cd /app && python -m pytest"
```

These cover the simulation/analytics engine (`services/simulation.py`), the
inheritance merge (`services/topology_chain.py`), and link validation. If any
test fails, STOP and report the failures — do not proceed to commit. If the
changes touched simulation or inheritance logic and no test covers the new
behavior, note that a test should be added before merging.

## 5. Frontend type-check (if `frontend/` changed)

Skip this step if the diff touched no `frontend/` files. Otherwise type-check.
Don't `exec` into the running `frontend` container: when the prod stack is up
it's the nginx bundle (no Node, so `npx` is missing), and the dev and prod
compose files share one image tag. Instead, build the dev image under its own
tag and run `tsc` against the working-tree source:

```bash
docker build -q -t phanero-frontend-typecheck -f frontend/Dockerfile frontend
docker run --rm -v "$PWD/frontend/src:/app/src:ro" phanero-frontend-typecheck sh -c "cd /app && npx tsc --noEmit"
```

Run from the repo root. The build is cached, so it's quick unless
`package.json` changed. **Confirm `tsc` actually ran** before reading the
results: output like `npx: not found` or exit code 127 means nothing was
checked, so STOP and report it rather than treating it as clean. (`tsc` exits 2
when it finds errors, which is expected given the pre-existing ones.)

The repo has **pre-existing** `tsc` errors (e.g. `SimulationPanel.tsx`'s
MonteCarlo section), and the app builds via Vite which ignores them — so don't
treat a non-empty result as a hard failure. Instead, grep the output for the
files you actually changed and confirm you introduced **no new** errors. STOP
if you did.

## 6. Prepare the commit

If the prior steps are clean, propose a commit message derived from the actual
diff and show it to me. Wait for my confirmation before committing.

> To make this auto-commit instead of stopping, delete this confirmation
> sentence and tell the step to commit directly.