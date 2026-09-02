---
name: running-checks
description: Run this repo's lint, format and type checks the way CI does. Use before every push, and whenever a change touches Python under edb/ or tests/.
---

# Running the checks

There are **three**, and all three gate CI. Run all three; do not stop at ruff.

```
uv run --no-sync ruff check edb tests
uv run --no-sync ruff format --check --diff edb tests
uv run --no-sync mypy --config-file pyproject.toml edb tests
```

Those are CI's commands verbatim (`.github/workflows/tests.yml`, jobs
`python-lint` and `python-typecheck`), so a clean run here means a clean
run there.

## Use `uv run --no-sync`, not a bare `uv run`

Bare `uv run ruff …` starts **building gel-server** — a ~7 minute Cython
and Rust build — because uv syncs the project before running. `--no-sync`
uses the environment as it stands.

Never invoke a bare `ruff` or `mypy` off `PATH` either. The pinned
versions are `ruff==0.11.2` and `mypy==1.13.0`; a newer ruff on `PATH`
disagrees with the repo on around a dozen files and will silently
reformat hunks your change never touched. Confirm what you are running:

```
uv run --no-sync ruff --version     # must print 0.11.2
uv run --no-sync mypy --version     # must print 1.13.0
```

## mypy is the one that gets skipped

It is slower than ruff and easy to forget, and it is the check that has
caught the most real breakage here. Two separate pushes went red for
exactly this — ruff was run, mypy was not.

It matters most where you would least expect it: **mypy does not check
the bodies of unannotated functions.** Adding an annotation to a helper
makes errors that were always there appear for the first time, so a
change that "only adds types" can surface a dozen failures.

## When a check fails

- `ruff format --check` → `uv run --no-sync ruff format <the files it named>`.
  Format only the files your change touched, then re-read the diff: if it
  reformatted something you did not edit, you are on the wrong ruff version.
- Some test files are deliberately excluded from `ruff format`. If you are
  about to reformat one of those, read the `docstring-tests` skill first.
- `mypy` reports both a line **and a column**. Read the column. Two fixes
  in this repo went in at the wrong place because only the line was read.

## Fast, targeted runs

All three accept paths, so a single file is quick:

```
uv run --no-sync ruff check edb/schema/utils.py
uv run --no-sync mypy --config-file pyproject.toml edb/schema/utils.py
```

Run the whole `edb tests` set before pushing regardless — mypy is a
whole-program check and a per-file run can miss what a full run catches.
