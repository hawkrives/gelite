# Milestone 0: Demolition — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Strip the Postgres-specific subsystems out of the fork and isolate the remaining backend into `edb/sqlite/`, leaving a tree that still builds and still passes the test suite.

**Architecture:** Work outside-in. First capture behaviour that is about to lose its only test (branching). Then cut the frontend's four leaks into the backend, so the frontend has no backend dependency at all. Then fork `edb/pgsql/` to `edb/sqlite/` and delete the subsystems the design drops. No SQLite code is written in this milestone — it ends with a Postgres-backed tree that is *shaped* for the port.

**Tech Stack:** Python 3.12, Cython, Rust (PyO3), `edb test` runner, jinja-rendered GitHub Actions workflows.

---

## Scope of this plan

This plan covers **Milestone 0 only**, from §11 of
`docs/superpowers/specs/2026-08-30-gel-on-sqlite-design.md`.

The spec's remaining milestones are deliberately **not** planned here:

- **Milestone 2** is a probe whose *output is a decision*, and §10 risk 1 says
  that decision can force a redesign of §4. Planning Milestone 3 before it
  reports would be planning against an unknown.
- **Milestones 3–7** each warrant their own spec → plan cycle once the
  preceding one has reported. Writing bite-sized tasks for them now would be
  speculation dressed as a plan.

Milestone 1 (vertical slice) is plannable immediately after this one lands.

## Before you start

Read `docs/superpowers/specs/2026-08-30-gel-on-sqlite-design.md`, at minimum
§1 (what gets deleted and why), §9a (what CI has already established), and §11
(milestones). This plan executes §1.

**Two facts that will otherwise waste your time:**

1. `.github/workflows/*.yml` are **generated** from `.github/workflows.src/`
   by `render.py`, driven by `.github/Makefile`. They carry no "do not edit"
   header. Edit the templates and run `make -B -C .github`; editing the
   generated files is silently reverted.
2. The repo requires **Python 3.12**. A 3.11 interpreter cannot even parse
   files using PEP 695 generics (`def field[T](...)`), so a syntax check with
   the wrong interpreter reports false failures.

**Running tests:** `edb test -k <test_name>` for one test,
`edb test -k <prefix>` for a group. `edb test --help` for more.

---

## Task 1: Capture branching behaviour before it can regress

§10 risk 8. `check_branching` was deleted along with the dump/restore
scaffolding it lived in. It never tested dump — it tested that a schema or data
branch faithfully reproduces its source. §6 of the spec makes a branch a
*separate SQLite file copied from a template*, so this is a v1 feature with a
novel implementation and currently nothing asserting it.

**This is not red-green TDD.** Branching already works on the Postgres backend.
The test must **pass on first run** — that is the whole point. It is a
characterisation test, written now so the port cannot silently change behaviour
later. If it fails on the current backend, you have found a real bug and should
stop and report it rather than adjust the test to match.

**Files:**
- Create: `tests/test_branching.py`

**Step 1: Write the test**

```python
import json

from edb.testbase import server as tb


class TestBranching(tb.QueryTestCase):
    """Characterisation tests for schema and data branches.

    Branching already works on the Postgres backend; these tests exist to
    pin that behaviour down before the SQLite port changes how branches
    are created. Section 6 of the design makes a branch a separate SQLite
    file copied from a template, which is a new implementation of an
    existing guarantee.

    These replace coverage lost with check_branching, which was deleted
    along with the dump/restore test scaffolding it happened to live in.
    """

    SETUP = '''
        create type Widget {
            create required property name: str;
        };
        insert Widget { name := 'w1' };
    '''

    async def _check_branch(self, branch_type: str, expect_rows: int) -> None:
        if not self.has_create_database:
            self.skipTest('create branch is not supported by the backend')

        orig = self.get_database_name()
        new = f'branchtest_{branch_type}_{orig}'
        orig_schema = await self.con.query_single('describe schema as sdl')

        await self.con.execute(
            f'create {branch_type} branch {new} from {orig}'
        )
        try:
            con2 = await self.connect(database=new)
        except Exception:
            await tb.drop_db(self.con, new)
            raise

        oldcon = self.con
        self.__class__.con = con2
        try:
            # Schema equality is asserted via migration rather than by
            # comparing SDL text. SDL rendering order is not stable, so a
            # text comparison would be flaky. A migration to an identical
            # schema has no work to do and reports complete immediately.
            with self.ignore_warnings():
                await self.con.execute(
                    f'start migration to {{ {orig_schema} }}'
                )
            status = json.loads(
                await self.con.query_single_json(
                    'describe current migration as json'
                )
            )
            self.assertTrue(
                status.get('complete'),
                f'{branch_type} branch schema differs from its source',
            )
            await self.con.execute('abort migration')

            self.assertEqual(
                await self.con.query_single('select count(Widget)'),
                expect_rows,
            )
        finally:
            self.__class__.con = oldcon
            await con2.aclose()
            await tb.drop_db(self.con, new)

    async def test_branching_schema_branch_01(self):
        # A schema branch copies the schema and none of the rows.
        await self._check_branch('schema', expect_rows=0)

    async def test_branching_data_branch_01(self):
        # A data branch copies the schema and the rows.
        await self._check_branch('data', expect_rows=1)
```

**Step 2: Run it — it must PASS**

Run: `edb test -k test_branching`

Expected: 2 tests, both pass.

If either fails, **stop**. Either the test is wrong (fix it) or branching is
broken on the current backend (report it — do not weaken the assertion).

**Step 3: Commit**

```bash
git add tests/test_branching.py
git commit -m "Add branching characterisation tests

Restores coverage lost when check_branching was deleted with the
dump/restore scaffolding. Schema fidelity is asserted by starting a
migration to the source schema and requiring it to report complete
immediately, which is stabler than comparing SDL text.

Branches become separate SQLite files in Section 6 of the design, so
this pins the behaviour before that implementation lands."
```

---

## Task 2: Cut the frontend's four leaks into the backend

The frontend (`edb/edgeql/`, `edb/ir/`, `edb/schema/`) must not import the
backend, so the backend can be forked and replaced without touching it. §1 of
the spec lists four shallow leaks. All four are deferred imports inside
function bodies.

**Files:**
- Modify: `edb/schema/types.py:3259`
- Modify: `edb/schema/utils.py:202`, `edb/schema/utils.py:1620`
- Modify: `edb/edgeql/compiler/config_desc.py:41`
- Modify: `edb/common/debug.py:224`

**Step 1: Look at each leak before changing anything**

```bash
grep -n "edb\.pgsql" edb/schema/types.py edb/schema/utils.py \
  edb/edgeql/compiler/config_desc.py edb/common/debug.py
```

Read the surrounding function in each case. They are not all the same shape:
`debug.py` uses the backend's SQL pretty-printer for debug output;
`config_desc.py` and `utils.py:202` want name mangling; `types.py:3259` and
`utils.py:1620` reach for type lookup and the compiler entry point.

**Step 2: Invert each one**

The general move is to pass the backend-provided value in as a parameter, or
to move the helper into the backend and have the backend call the frontend.
Take them one at a time; do not batch.

For `edb/common/debug.py:224` specifically, the dependency is only for
formatting debug output — guard it so the frontend does not require the
backend to be importable:

```python
try:
    import edb.pgsql.codegen as _pg_codegen
except ImportError:  # backend not present
    _pg_codegen = None
```

**Step 3: Verify the frontend no longer imports the backend**

Run:
```bash
grep -rn "edb\.pgsql" edb/schema/ edb/edgeql/ edb/ir/ edb/common/ || echo CLEAN
```
Expected: `CLEAN`, or only the guarded import in `debug.py`.

**Step 4: Run the test suite for regressions**

Run: `edb test -k test_schema`
Expected: same pass/fail as before your change. Compare against a run on the
previous commit if unsure.

**Step 5: Commit**

```bash
git add edb/schema/types.py edb/schema/utils.py \
  edb/edgeql/compiler/config_desc.py edb/common/debug.py
git commit -m "Remove the frontend's imports of the Postgres backend

The parser, IR and schema layers no longer reach into edb/pgsql, so the
backend can be forked and replaced without touching them."
```

---

## Task 3: Fork `edb/pgsql/` to `edb/sqlite/`

Internal module names are preserved deliberately, so `git log --follow` keeps
working and upstream fixes stay findable by path.

**Step 1: Copy with history**

```bash
git mv edb/pgsql edb/sqlite
```

**Step 2: Update every importer**

```bash
grep -rln "edb\.pgsql\|edb/pgsql" --include=*.py --include=*.pyx \
  --include=*.pxd --include=*.pyi edb/ tests/ setup.py \
  | xargs sed -i 's/edb\.pgsql/edb.sqlite/g; s|edb/pgsql|edb/sqlite|g'
```

**Step 3: Check for stragglers**

Run:
```bash
grep -rn "edb\.pgsql\|edb/pgsql" --include=*.py --include=*.pyx \
  --include=*.pxd --include=*.pyi --include=*.toml --include=*.cfg \
  . | grep -v '^\./\.git/' || echo CLEAN
```
Expected: `CLEAN`. Check `setup.py` and `pyproject.toml` by hand for package
lists and Cython extension paths that a plain `grep` for the dotted name misses.

**Step 4: Verify it still builds**

Run: `python setup.py -q ci_helper --type ext`
Expected: a hash, not a traceback.

**Step 5: Commit**

```bash
git add -A
git commit -m "Fork edb/pgsql to edb/sqlite

Internal module names are preserved so history stays followable and
upstream fixes remain findable by path. No behaviour change."
```

---

## Task 4: Delete the wire protocol and cluster management

§1. SQLite is in-process: there is no wire protocol, no connection pool, and
no server to supervise.

**Step 1: Delete**

```bash
git rm -r edb/server/pgcon edb/server/pgcluster.py \
  edb/server/pgconnparams.py rust/pgrust rust/conn_pool
```

**Step 2: Remove the Rust workspace members**

Modify `Cargo.toml` — drop `"rust/conn_pool"` and `"rust/pgrust"` from
`[workspace] members`, and the `conn_pool` / `pgrust` entries from
`[workspace.dependencies]`.

**Step 3: Find what breaks**

Run:
```bash
grep -rn "pgcon\|pgcluster\|pgconnparams\|conn_pool\|pgrust" \
  --include=*.py --include=*.pyx --include=*.toml edb/ rust/ Cargo.toml \
  | grep -v '^\./\.git/'
```

Every hit is a call site that now needs removing or stubbing. Expect
`edb/server/tenant.py`, `edb/server/server.py` and `edb/server/bootstrap.py`
to be the bulk of it. Work through them one file at a time, committing per
file — this is the largest single deletion in the milestone and a broken
half-state is hard to bisect.

**Step 4: Verify**

Run: `python -c "import edb.server.main"`
Expected: no `ImportError`.

**Step 5: Commit per file as you go**

```bash
git commit -m "Delete the Postgres wire protocol and cluster management

SQLite is in-process: no wire protocol, no connection pool, no server
process to supervise."
```

---

## Task 5: Delete multi-tenancy, HA, and the compiler pool

§1. A local-first database has one tenant, no failover, and no need to
distribute compilation across processes.

**Step 1: Delete**

```bash
git rm -r edb/server/multitenant.py edb/server/ha edb/server/consul.py \
  edb/server/compiler_pool
```

**Step 2: Find and remove call sites**

```bash
grep -rn "multitenant\|compiler_pool\|consul\|\bha\b" \
  --include=*.py --include=*.pyx edb/ | grep -v '^\./\.git/'
```

`edb/server/args.py` will have CLI options for these; remove the options along
with the code they drive.

**Step 3: Verify**

Run: `python -c "import edb.server.main"` then `edb test -k test_server_ops`
Expected: import succeeds; tests pass or fail exactly as they did before.

**Step 4: Commit**

```bash
git commit -m "Delete multi-tenancy, HA and the compiler pool

One tenant, no failover, no cross-process compilation."
```

---

## Task 6: Delete SQL-over-the-wire, extensions, and the upgrade ladder

§1 and §5. The resolver serves Postgres-protocol clients, which is out of
scope. The extensions and the in-place upgrade ladder both belong to a
lineage this fork is not continuing.

**Step 1: Delete**

```bash
git rm -r edb/sqlite/resolver edb/sqlite/delta_ext_ai.py \
  edb/sqlite/deltafts.py edb/lib/ext edb/sqlite/patches.py \
  edb/sqlite/patches_6x.py edb/lib/pg.edgeql edb/lib/net.edgeql
```

(Note the paths are under `edb/sqlite/` now, after Task 3.)

**Step 2: Exclude the deferred stdlib modules from the build**

§5 defers ranges and full-text search. Their sources stay so v2 can restore
them and so their tests xfail rather than error — they are removed from the
*build*, not from the tree:

- `edb/lib/fts.edgeql`
- `edb/lib/std/31-rangefuncs.edgeql`

Find where the stdlib module list is assembled (start at
`edb/server/bootstrap.py`, look for how `edb/lib` is walked) and exclude these
two.

**Step 3: Find and remove call sites**

```bash
grep -rn "resolver\|ext_ai\|deltafts\|patches\|std::pg\|std::net" \
  --include=*.py --include=*.pyx --include=*.edgeql edb/ \
  | grep -v '^\./\.git/'
```

**Step 4: Verify the stdlib still bootstraps**

Run: `edb test -k test_edgeql_select_unique_01`
Expected: PASS. This exercises a full server bootstrap, so it is the cheapest
proof that the stdlib still assembles after removing modules from it.

**Step 5: Commit**

```bash
git commit -m "Delete the SQL resolver, extensions and the upgrade ladder

SQL-over-the-wire is out of scope. Extensions and the in-place upgrade
ladder belong to a lineage this fork is not continuing. Ranges and FTS
are excluded from the build rather than deleted, so v2 can restore them."
```

---

## Task 7: Delete the CI workflows whose subsystems are gone

Seven workflows test subsystems that Tasks 4–6 deleted.

**Step 1: Delete templates and generated output together**

```bash
git rm .github/workflows.src/tests.ha.tpl.yml \
  .github/workflows.src/tests.ha.targets.yml \
  .github/workflows.src/tests.pool.tpl.yml \
  .github/workflows.src/tests.pool.targets.yml \
  .github/workflows.src/tests.managed-pg.tpl.yml \
  .github/workflows.src/tests.managed-pg.targets.yml \
  .github/workflows.src/tests.pg-versions.tpl.yml \
  .github/workflows.src/tests.pg-versions.targets.yml \
  .github/workflows.src/tests.inplace.tpl.yml \
  .github/workflows.src/tests.inplace.targets.yml \
  .github/workflows.src/tests.inplace7x.tpl.yml \
  .github/workflows.src/tests.inplace7x.targets.yml \
  .github/workflows.src/tests.patches.tpl.yml \
  .github/workflows.src/tests.patches.targets.yml \
  .github/workflows/tests.ha.yml .github/workflows/tests.pool.yml \
  .github/workflows/tests.managed-pg.yml \
  .github/workflows/tests.pg-versions.yml \
  .github/workflows/tests.inplace.yml \
  .github/workflows/tests.inplace7x.yml \
  .github/workflows/tests.patches.yml
git rm -r .github/scripts/patches
```

**Step 2: Drop them from the Makefile**

Modify `.github/Makefile` — remove those seven from the `all:` target.

**Step 3: Regenerate and confirm nothing else moved**

Run:
```bash
make -B -C .github && git status --short
```
Expected: only the deletions staged. If a *surviving* workflow changed, your
template edit had a side effect — investigate before committing.

**Step 4: Confirm the survivors still parse**

Run:
```bash
python -c "
import yaml, pathlib
for p in sorted(pathlib.Path('.github/workflows').glob('*.yml')):
    yaml.safe_load(p.read_text())
print('all workflows parse')
"
```
Expected: `all workflows parse`

**Step 5: Commit**

```bash
git add -A
git commit -m "Delete CI workflows for the subsystems that are gone

HA, connection pool, managed Postgres, Postgres versions, the in-place
upgrade ladder and patch testing. Templates and generated output removed
together, and dropped from .github/Makefile."
```

---

## Task 8: Verify the milestone is actually complete

**Step 1: The tree builds**

Run: `python setup.py -q ci_helper --type rust`
Expected: a hash, not a traceback.

**Step 2: No Postgres dependency survives outside the backend**

Run:
```bash
grep -rn "pgcon\|pgcluster\|multitenant\|compiler_pool\|resolver" \
  --include=*.py --include=*.pyx edb/ | grep -v "^edb/sqlite/" || echo CLEAN
```
Expected: `CLEAN`.

**Step 3: The suite still passes**

Run: `edb test`

Expected: the same result as §9a's baseline — around 854 tests per shard with
no failures, now that dump/restore and pgvector are gone. **Compare against the
baseline, not against zero.** A test that failed before this milestone and
still fails is not your regression; a test that passed before and fails now is.

**Step 4: Branching still works**

Run: `edb test -k test_branching`
Expected: 2 passes. These are the tests from Task 1 and they must not have
regressed.

**Step 5: Commit and open the PR**

```bash
git commit --allow-empty -m "Milestone 0 complete: demolition"
```

---

## What this milestone deliberately does not do

- No SQLite code. The tree still runs on Postgres at the end of Milestone 0.
- No renaming of lowercase on-disk names (`edgedb` database,
  `edgedb_supergroup` role, `edgedbpub`/`edgedbstd` schemas). §2 keeps those
  until the storage layer is actually rewritten.
- No changes to `edb/edgeql/`, `edb/ir/` or `edb/schema/` beyond Task 2's
  four leaks.

## Next

Milestone 1 (vertical slice, read path) is plannable as soon as this lands.
Milestone 2's probe should be started in parallel with it — §10 risk 1 rates
the constraint-atomicity question High, and its answer shapes Milestone 3.
