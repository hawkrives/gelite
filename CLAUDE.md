# gelite

A hard fork of Gel (EdgeDB) that replaces the PostgreSQL backend with
SQLite. The design is
`docs/superpowers/specs/2026-08-30-gel-on-sqlite-design.md`; the current
milestone plan is `docs/plans/2026-08-30-milestone-0-demolition.md`.
Work is tracked in GitHub issues, grouped under milestone issues (#28 is
Milestone 0).

Upstream names survive throughout — `edgedb`, `edb`, `EdgeQL`. The
package is `edb/`.

## Skills

Repo-local skills in `.claude/skills/` cover the things that have gone
wrong before:

| skill | when |
|---|---|
| `running-checks` | before every push |
| `running-tests` | the runner is not pytest, and the obvious invocation lies |
| `dev-environment` | fresh checkout, container restart, unexplained import failure |
| `docstring-tests` | before formatting or editing the docstring-data test files |
| `deleting-a-subsystem` | any Milestone 0 demolition task |

## Layout

- `edb/edgeql/`, `edb/ir/`, `edb/schema/` — the frontend.
- `edb/pgsql/` — the backend, to be forked to `edb/sqlite/` (issue #31).
- `edb/server/` — server, compiler, connection handling.
- `edb/common/` — shared infrastructure, below both layers.
- `edb/lib/*.edgeql` — the standard library, compiled at build time.

**The frontend must not import the backend.** Nothing under `edb/common`,
`edb/edgeql`, `edb/ir` or `edb/schema` may import `edb.pgsql`, at module
scope or inside a function body;
`tests/test_sourcecode.py::test_cqa_frontend_does_not_import_backend`
fails the build if it does. When the frontend needs an answer only the
backend has, add a hook to `edb/schema/backend.py` and register it from
`edb/pgsql/common.py`.

`edb/pgsql/__init__.py` must stay empty. `setup.py`'s cache-key step does
`find_spec('edb.pgsql.metaschema')`, which imports the parent package
before the Rust extension exists; anything there that reaches
`edb.schema` fails the build. `EMPTY_INIT_FILES` in
`tests/test_sourcecode.py` enforces it.

## Tooling

`uv` throughout. Run tools as `uv run --no-sync <tool>` — a bare
`uv run` syncs the project first and triggers a ~7 minute build. Pinned:
`ruff==0.11.2`, `mypy==1.13.0`; a newer ruff on `PATH` disagrees with the
repo and will reformat files your change never touched.

Both lockfiles are validated in CI and must be regenerated with their own
tooling, never by hand:

- `Cargo.lock` — `cargo metadata --locked` in the `rust-rustfmt` job
- `uv.lock` — `uv lock --check` in the `python-lint` job

## CI

`.github/workflows/tests.yml`, edited **directly** — the old
`.github/workflows.src/` Jinja templates are deleted, and edits to the
generated file used to be silently reverted.

Jobs: `build`, `cargo-test`, `rust-clippy`, `rust-rustfmt`,
`python-lint`, `python-typecheck`, `python-test` (16 shards),
`python-test-list`, and `test-conclusion`, which gates on all of them and
renders the shard results. `build` failing means no shards run, so
`test-conclusion` fails too — with "no result files were found", which is
a consequence, not an independent failure.

The full matrix takes roughly 50 minutes. `.github/time_stats.csv` holds
per-test times; consult it before starting a suite locally.

## Conventions

- Branch from `master`; open pull requests as drafts.
- Never skip, disable or quarantine a test to get CI green.
- A local pass is weaker evidence than it looks: a working checkout has
  the project installed and built, while CI's `build` job has
  dependencies installed and the project *not* built.
- Prefer a characterisation test proven against the pre-change tree over
  an assertion written after the fact.
