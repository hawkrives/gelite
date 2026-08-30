# Gel on SQLite — Design

**Date:** 2026-08-30
**Status:** Approved design, pending implementation plan
**Scope:** Architectural

## Context

`gelite` is a hard fork of `geldata/gel`. Upstream is unfunded and inactive; this
fork will not merge from it again. The goal is a Gel that runs on SQLite instead
of PostgreSQL, usable as the database in local-first applications.

Because the fork is permanent, this design deletes rather than abstracts. There
is no backend abstraction layer and no dual-backend support. SQLite is the only
backend.

## Goals

- EdgeQL, schema, and migrations working against a single SQLite file.
- No PostgreSQL process, no separate server to install or supervise.
- Correct for application development: silent wrong answers are unacceptable,
  missing features are acceptable and tracked.

## Non-goals

- Feature parity with Gel-on-Postgres.
- SQL-over-the-wire (the Postgres protocol front end).
- Multi-tenancy, high availability, connection pooling.
- Multi-device sync in v1 (see "Sync affordances" — the door is held open).

## Decisions taken

| Decision | Choice |
|---|---|
| Upstream relationship | Hard fork. Never merge again. |
| Compiler strategy | Retarget the existing PG compiler, not a rewrite. |
| Delivery, phase 1 | CPython sidecar speaking the existing Gel binary protocol. |
| Delivery, phase 2 | Thin native runtime executing serialized `QueryUnit`s. |
| Sync | Not in v1, but storage layout stays sync-friendly. |
| SQLite driver | `apsw`, not stdlib `sqlite3`. |
| JSON representation | SQLite JSONB (requires SQLite >= 3.45). |
| Deferred to v2 | Ranges/multiranges, `decimal`/`bigint`, full-text search. |

### Why retarget rather than rewrite

Two findings in the existing code make retargeting substantially cheaper than a
fresh IR-to-SQLite lowering, and both are load-bearing for this design:

1. **`OverlayEntry` already accepts a plain relation.**
   `edb/pgsql/compiler/context.py:92` types it as
   `tuple[OverlayOp, pgast.BaseRelation | pgast.CommonTableExpr, irast.PathId]`,
   and the consumer at `edb/pgsql/compiler/relctx.py:1754` only calls
   `rvar_for_rel(cte, ...)` without inspecting which kind it received. So the
   entire overlay read path — access policies, rewrites, triggers, nested DML —
   works unchanged when DML produces temp tables instead of CTEs.

2. **`_lateral_union_join` builds correlations as top-level equalities.**
   `edb/pgsql/compiler/relctx.py:1470` constructs
   `astutils.join_condition(lref, rref)` per path bond, pushes the conjunction
   into the subquery's `WHERE`, then cross-joins. Gel's laterals are therefore
   already structurally equality joins, and decorrelation is a change to one
   function rather than a compiler-wide rewrite.

The alternative — a fresh lowering — would require re-deriving cardinality
inference, optionality, path factoring, volatility handling, and object-identity
`DISTINCT` semantics. Its failure mode is silently wrong answers, which is the
one outcome this project cannot absorb.

### The phase-2 invariant

**Nothing required at execution time may live outside the `QueryUnit`.**

`edb/server/compiler/dbstate.py:230` already defines `QueryUnit` with `sql: bytes`,
`in_type_data`/`out_type_data` (Gel binary type descriptors), `cardinality`, and
`is_transactional`. It is already a serializable compilation result. Phase 2 is
therefore an extraction — "execute a serialized `QueryUnit` against SQLite" — not
a rewrite.

This invariant forbids any design that depends at query time on server-side
stored procedures, session state, or live Python schema objects. It is cited
throughout this document as the reason for several otherwise-arbitrary choices.

---

## 1. Code layout and demolition

`edb/pgsql/` is copied to `edb/sqlite/` with internal module names preserved
(`compiler/`, `dbops/`, `delta.py`, `types.py`, `common.py`, `ast.py`,
`codegen.py`), so `git log --follow` continues to work.

Only 35 files outside `edb/pgsql/` import it. Four are in the frontend and all
are shallow deferred imports, to be inverted first so the frontend has no
backend dependency:

- `edb/schema/types.py:3259` — type lookup
- `edb/schema/utils.py:202,1620` — name mangling and compiler entry
- `edb/edgeql/compiler/config_desc.py:41` — name mangling
- `edb/common/debug.py:224` — SQL pretty-printing

### Deleted before any SQLite work begins

- `edb/pgsql/resolver/` — SQL-over-the-wire.
- `edb/server/pgcon/` (~4.2k lines Cython), `pgcluster.py`, `pgconnparams.py`,
  `rust/pgrust`, `rust/conn_pool` — there is no wire protocol and no pool.
- `multitenant.py`, `ha/`, `consul.py`, `edb/server/compiler_pool/`.
- `delta_ext_ai.py`, `deltafts.py`, `edb/lib/ext/`, `patches.py`, `patches_6x.py`.
- `edb/lib/pg.edgeql` (`std::pg::*` types — meaningless without Postgres) and
  `edb/lib/net.edgeql` (`std::net`).

Roughly 25–30k lines, each of which would otherwise require a SQLite answer.

### Excluded from the build, not deleted

Deferred features keep their source so v2 can restore them by re-including it,
and so their tests xfail rather than error:

- `edb/lib/fts.edgeql` — full-text search.
- `edb/lib/std/31-rangefuncs.edgeql` — ranges and multiranges.

`decimal` and `bigint` cannot be excluded at file granularity — their bindings
are spread across the numeric, cast, and converter modules — so they are
deferred by leaving the scalar types declared but unimplemented, which surfaces
as an explicit unsupported-type error rather than a wrong answer.

### Preserved unchanged

`edb/edgeql/`, `edb/ir/`, `edb/schema/`, `edb/server/protocol/` (the phase-1
sidecar), `edb/graphql/` (free — it lowers to EdgeQL).

### Preserved, rewritten

`edb/lib/` keeps its schema declarations; its 649 SQL bindings are retargeted
(Section 5).

### New

`edb/server/sqlitecon/` replaces `pgcon/`, deliberately matching the existing
interface so `dbview/` and `protocol/` are untouched.

### CI workflows

Seven of the seventeen workflows in `.github/workflows/` test subsystems this
section deletes, and become dead on the same commit:

- `tests.ha.yml` — high availability
- `tests.pool.yml` — connection pool
- `tests.managed-pg.yml`, `tests.pg-versions.yml` — Postgres itself
- `tests.inplace.yml`, `tests.inplace7x.yml`, `tests.patches.yml` — the
  in-place upgrade ladder (`patches.py`, `patches_6x.py`)

`docs.yml` and `docs-preview-deploy.yml` post to upstream's Vercel deploy hook
via a secret this fork does not have, so they are dead for a different reason.

The remainder (`tests.yml`, `tests.reflection.yml`, the `build.*` pipeline)
need rework rather than deletion, and Section 8's differential-oracle job has no
workflow at all yet.

**Actions is currently disabled on this fork**, which is the GitHub default for
forks. Nothing runs on any pull request until it is enabled. This has to be
settled before Milestone 0 begins producing code, or the Milestone 6 green gate
has no automation behind it.

---

## 2. Storage mapping

Gel's physical layout is already SQLite-shaped and does not change:

- One table per object type, named by the type's UUID, keyed by `id`.
- Single-cardinality pointers become columns on the source table, named by the
  pointer's UUID (`edb/pgsql/types.py:539`, `get_pointer_storage_info`).
- Multi pointers, and links with link properties, get their own table with
  `source`/`target` columns.
- Inheritance becomes a per-type view unioning the type with its descendants
  (`edb/pgsql/inheritance.py`).

### Namespacing

SQLite has no schemas. `convert_name()` in `common.py` is the single choke point
for backend identifiers: flatten `edgedbpub.<uuid>` to `edgedbpub_<uuid>`.
`ATTACH` is rejected — it complicates transaction scope and turns one database
into several files.

### Scalar representation

| Gel type | Postgres today | SQLite |
|---|---|---|
| `uuid` | native `uuid` | `BLOB(16)` — memcmp ordering matches PG's |
| `array<T>` | PG array | JSONB array |
| tuples | one `CREATE TYPE` composite per shape | JSONB (array for positional, object for named) |
| `datetime`, `duration` | domains over `timestamptz`/`interval` | `INTEGER` microseconds |
| `cal::local_date` | domain over `date` | `INTEGER` days |
| `json` | `jsonb` | JSONB, canonicalized on write (see below) |
| `bytes` | `bytea` | `BLOB` |
| `decimal`, `bigint` | `numeric` | Deferred to v2; `TEXT` reserved |

Domains disappear. Constraints they carried move into emitted `CHECK`
constraints where static, and into compiler-emitted validation otherwise.

`array<T>` maps cleanly: `unnest` becomes `json_each`, `array_agg` becomes
`jsonb_group_array`. Tuple element access `t.0` becomes
`jsonb_extract(col, '$[0]')`; the compiler knows the static element type from IR
and re-casts on extraction. Tuple elements are not indexable — an accepted loss.

### JSONB, and how it differs from Postgres

SQLite JSONB (3.45+, Jan 2024) stores the parsed form, so `jsonb_extract` and
`json_each` skip reparsing. Because tuples and arrays are JSON internally, this
is the hot path for the type system, not an occasional optimization.

**This sets a floor of SQLite >= 3.45 and requires bundling SQLite** rather than
linking the system library. System SQLite on Android and older iOS is behind.
apsw bundles an amalgamation, so this aligns with the driver choice.

SQLite JSONB is **not** PostgreSQL `jsonb`:

- PG canonicalizes (sorts object keys, drops duplicates, normalizes numbers);
  SQLite preserves key order and duplicates. Byte equality is therefore not
  semantic JSON equality.
- PG defines a total order across JSON types
  (Object > Array > Boolean > Number > String > Null); SQLite JSONB is a `BLOB`,
  so `<` is memcmp over the binary encoding — meaningless.

This collides with `edb/lib/std/30-jsonfuncs.edgeql:148-230`, where all six
comparison operators on `std::json` are bare passthrough
(`USING SQL OPERATOR '='`, `'<'`, …). Ported naively,
`{"a":1,"b":2} = {"b":2,"a":1}` returns false and `ORDER BY json` is arbitrary.

**Resolution, two parts:**

1. **Canonicalize `std::json` on write** — sort keys, drop duplicates, normalize
   numbers. This makes gelite match Gel-on-Postgres behavior rather than
   inventing new semantics.
2. The six comparison operators become deterministic UDFs implementing PG's
   ordering, covering cross-representation cases.

Tuples and arrays are exempt: the compiler generates them, so key order is
consistent per shape, duplicates cannot occur, and Gel only compares same-typed
tuples. Blob comparison is sound there.

### Sync affordances

Stable row identity — normally the hardest thing to retrofit — is already free,
since every object table is keyed by UUID. Beyond that, exactly two additions:

1. `__version INTEGER NOT NULL DEFAULT 0` on every object table, bumped by an
   `AFTER UPDATE` row trigger against a global monotonic counter.
2. A single `__deleted(id BLOB, type_id BLOB, ts INTEGER)` table, written by
   `AFTER DELETE` triggers.

**Explicitly rejected: soft-delete columns.** A `deleted_at IS NULL` predicate
would have to be injected into every emitted query forever, for a feature that
may never be built. The tombstone table carries identical information at zero
read-path cost and drops cleanly if sync never happens.

---

## 3. Query lowering

Implemented as a **`pgast` -> `pgast` normalization pass pipeline**, run after
compilation and before codegen. Each pass is independently testable — tree in,
tree out, no database. The compiler proper is modified only where the change is
structural rather than shape-level, which in practice means DML alone
(Section 4).

The passes hold no reference to live schema objects, so they stay entirely on
the build-time side of the phase-2 line.

### LATERAL removal

`_lateral_union_join` (`relctx.py:1470`) already builds the correlation as a
conjunction of top-level equalities on path bonds. Decorrelation attaches that
same AST node to a `JOIN ... ON` instead of pushing it into the subquery's
`WHERE` plus a cross join.

**Wrinkle:** the function injects per-component via `each_query_in_set`, because
each `UNION` component's `rref` is a different column reference. Per-component
predicates cannot be hoisted into a single `ON`. Fix: wrap the set operation in
a subquery projecting the bond columns under uniform names, then join against
the wrapper. Each component already produces the bond as an output var.

**Residual laterals** — correlation reaching into `LIMIT` or `ORDER BY`
(`.foo LIMIT 1`, volatility isolation) — cannot decorrelate. Fallbacks, in order:

1. A correlated scalar subquery in the select list, when it yields one column
   and one row (SQLite permits correlation there).
2. A temp table materialized per correlation key.

Fallback 2 is expected to be a correctness backstop rather than a hot path.
**This is an assumption to measure during the vertical slice, not to trust.**

### DISTINCT ON

`relgen.py:1793` (and the semi-join case at `1525`) sets `distinct_clause` to a
column list, which codegen renders as `DISTINCT ON`. SQLite has no equivalent.
Rewrite: wrap in a subquery with
`ROW_NUMBER() OVER (PARTITION BY <distinct cols> ORDER BY <sort clause>)` and
filter `= 1`. Window functions have been available since SQLite 3.25. When
`distinct_clause` covers the entire output list it degrades to plain
`SELECT DISTINCT`; the empty-tuple case already compiles to `LIMIT 1`.

### Set-returning functions

`unnest(arr)` becomes `json_each(arr)`; `WITH ORDINALITY` becomes
`json_each.key`. `generate_series` becomes a registered virtual table, which
apsw makes straightforward.

---

## 4. DML and overlays

Gel compiles DML to a single SQL statement: a `WITH` chain in which some CTEs
are `INSERT`/`UPDATE`/`DELETE ... RETURNING`, ending in a `SELECT`. SQLite
supports neither data-modifying CTEs nor capturing `RETURNING` output into a
table from within SQL.

### Materialize-then-mutate

A DML statement becomes an **ordered script inside one transaction**. The temp
table serves double duty: it is the `RETURNING` substitute *and* the overlay
relation.

| | Today | SQLite |
|---|---|---|
| INSERT | `INSERT ... SELECT <shape> RETURNING *` | `INSERT INTO tmp_ins SELECT <shape>;` then `INSERT INTO Target SELECT ... FROM tmp_ins;` |
| UPDATE | `UPDATE ... RETURNING *` | `INSERT INTO tmp_upd SELECT <range + new values>;` then `UPDATE Target SET ... FROM tmp_upd WHERE ...;` |
| DELETE | `DELETE ... RETURNING *` | `INSERT INTO tmp_del SELECT id, ... FROM <range>;` then `DELETE FROM Target WHERE id IN (SELECT id FROM tmp_del);` |

Two things make this fit the existing compiler rather than fight it:

- `gen_dml_cte` (`dml.py:240`) already generates a `SelectStmt` for updates —
  its own comment says "the contents select is the query that needs to join the
  range and include policy filters". "Compute the affected set first" is already
  the compiler's shape.
- SQLite has supported `UPDATE ... FROM` since 3.33, so updates need no rewrite
  into correlated subqueries.

Script ordering comes from the existing CTE list, which is already dependency
ordered (`ordered_type_ctes`, `check_ctes`).

Because the overlay consumer is agnostic (see "Why retarget"), access policies,
rewrites, triggers, and nested DML require no changes.

### Temp table lifecycle

**Temp tables are created once per query shape with a fixed column layout, and
each execution begins with `DELETE FROM tmp_...`.**

The naive alternative — `DROP TABLE IF EXISTS t; CREATE TEMP TABLE t AS ...` per
execution — is a performance trap that would be miserable to diagnose. Creating
or dropping a table is DDL; DDL bumps SQLite's schema cookie; a schema change
invalidates every prepared statement on the connection. A cached `QueryUnit`
re-executed with new parameters would re-prepare its entire script every time.

`pgast.Relation` already carries an `is_temporary` field
(`edb/pgsql/ast.py:265`). Temp table schemas are derivable at compile time and
are serialized into the `QueryUnit`, per the phase-2 invariant.

### Risk: constraint-check atomicity

A single Postgres statement is atomic with respect to constraint checking;
intermediate states are never visible to a unique index. A script is not.
Multi-statement sequences that rely on single-statement atomicity — link table
updates that delete then reinsert, exclusive constraints across a rewrite — can
transiently violate a unique index that Postgres never would.

Partial mitigation: `PRAGMA defer_foreign_keys = ON` defers foreign key checks
to `COMMIT` and, unlike `PRAGMA foreign_keys`, can be set inside a transaction
and auto-resets at transaction end. This restores Postgres semantics for foreign
keys specifically. **It does not help `UNIQUE` or `CHECK` constraints**, which
SQLite offers no way to defer.

This is the highest-severity unknown in the design. Milestone 2 probes it
concurrently with the DML implementation rather than ahead of it.

### Risk: UNLESS CONFLICT

SQLite has upsert (`ON CONFLICT DO UPDATE`, 3.24+), but
`insert_needs_conflict_cte` (`dml.py:1397`), `compile_insert_else_body`
(`dml.py:1457`), and `compile_insert_else_body_failure_check` (`dml.py:1622`)
are roughly 250 lines built on Postgres semantics. Budget this as its own
workstream rather than assuming the upsert syntax covers it.

---

## 5. Standard library

649 SQL bindings across `edb/lib/`:

| Form | Count | Cost |
|---|---|---|
| `USING SQL EXPRESSION` | 127 | Free — no SQL body; compiler special-cases these |
| `USING SQL OPERATOR` | 305 | Mostly free — scalar comparison and arithmetic map directly |
| `USING SQL FUNCTION` | 165 | ~70 distinct functions, triaged below |
| `USING SQL CAST` | 52 | Requires explicit checked implementations |

### Function triage (~70 distinct)

- **~30 native to SQLite** — `abs`, `lower`, `upper`, `replace`, `trim`, `max`,
  `min`, `random`, `count`, `sum`, `avg`, and the math set (`acos`…`tan`, `exp`,
  `ln`, `log`, `sqrt`, `pi`). Rename or identity.
- **~8 map onto SQLite JSON** — `array_agg` -> `jsonb_group_array`;
  `unnest`, `jsonb_array_elements`, `jsonb_each` -> `json_each`;
  `to_jsonb` -> `jsonb`; `jsonb_typeof` -> `json_type`.
- **~10 trivial UDFs** — `initcap`, `reverse`, `bool_and`/`bool_or`,
  `stddev`/`variance` family, `justify_days`/`justify_hours`.
- **1 virtual table** — `generate_series`.
- **~13 Gel-own `edgedb.*`** — `datetime_in`, `duration_in`, `local_date_in`,
  `str_to_bigint`, `str_to_decimal`, `uuid_generate_v4`, and similar. All
  parsers and formatters; already namespaced, so easy to enumerate.
- **~5 already dead** — `_all_role_memberships`, `_describe_roles_as_ddl`,
  `get_current_database`, `approximate_count`, `reset_query_stats`. Roles and
  stats were deleted in Section 1.

### Casts and arithmetic: the silent-wrongness surface

`USING SQL CAST` delegates to Postgres's cast, which raises on bad input.
**SQLite's does not:** `CAST('abc' AS INTEGER)` returns `0`, where Gel guarantees
`<int64>'abc'` raises `InvalidValueError`. Similarly, SQLite silently promotes
integer overflow to `REAL` where Gel raises `NumericOutOfRangeError`.

All 52 cast bindings become explicit checked expressions, and cast/overflow
behavior gets a dedicated test sweep rather than riding along with feature tests.
This is the failure class that produces plausible wrong data rather than errors.

### UDF budget

Every UDF registered in Python needs a Rust twin in phase 2, so the UDF count is
a direct phase-2 line item — currently tracking ~40. **Rule: prefer an inline
SQL expression body over a UDF; a UDF must earn its place.**

apsw registers deterministic UDFs cleanly, and deterministic functions are usable
in indexes, which matters for indexed computed properties.

### Deferred to v2

- **Ranges and multiranges** — native PG types, 134 lines of bindings plus their
  own cast set, no SQLite analogue. Would need a JSON encoding plus a UDF suite
  for containment, overlap, and normalization.
- **`decimal` and `bigint`** — arbitrary precision. Would need `TEXT` storage, a
  custom collation for correct `ORDER BY` and indexes, and decimal arithmetic
  behind every operator. Largest correctness surface per unit of user value.
  `TEXT` is reserved as the storage form now, so adding them later needs no data
  migration.
- **Full-text search** — SQLite FTS5 is capable but semantically quite different
  from `tsvector`/`tsquery`. A port, not a mapping.

---

## 6. DDL and migrations

`dbops` is already a command-object layer (`CreateTable`, `AlterTableAddColumn`,
`AlterTableAlterColumnType`, `AlterTableAddConstraint`, `DropTable`, …) that
generates SQL. The table-rebuild strategy lives entirely inside how those objects
emit SQL. **`delta.py`'s 7,756 lines do not change.**

| dbops command | SQLite |
|---|---|
| `AlterTableAddColumn` | Native |
| `AlterTableDropColumn` | Native (3.35+) |
| `RenameObject` (table/column) | Native |
| `AlterTableAlterColumnType` | Rebuild |
| `AlterTableAddConstraint` / `DropConstraint` | Rebuild |
| `AlterTableAlterColumnNull` / `AlterColumnDefault` | Rebuild |

### Rebuild procedure

Create the new table under a temporary name, `INSERT ... SELECT` across, drop the
old, rename, recreate indexes/triggers/views, `PRAGMA foreign_key_check`.

Two gotchas:

- **`PRAGMA foreign_keys` is a no-op inside a transaction** and must be toggled
  outside it. `dbops/ddl.py:37` already defines `NonTransactionalDDLOperation`,
  and `QueryUnit` already carries `early_non_tx_sql`.
- **`PRAGMA legacy_alter_table` must be ON during the rename.** With it off,
  SQLite rewrites references to the renamed table inside view and trigger
  definitions — exactly wrong mid-rebuild, when those views are about to be
  recreated.

Every rebuild invalidates the `inhview`s referencing that table, and SQLite
tracks no view dependencies — it leaves views pointing at dropped tables and
fails only on use. The rebuild drops and recreates the affected views;
`inheritance.py` already computes that set.

### Batching

`AlterTable` is already a `CompositeCommand` holding a list of fragments.
Partition fragments into natively-supported and rebuild-requiring; if any
fragment requires a rebuild, perform exactly one rebuild applying all of them.
A migration changing five columns rebuilds once.

### Database creation is a file copy

Gel already bootstraps through template databases
(`_create_edgedb_template_database` at `bootstrap.py:464`,
`dbops.CreateDatabase(db, template=tpl_db)` at `bootstrap.py:2361`), and
`QueryUnit` already has a `create_db_template` field. On SQLite a template
database is a file.

The stdlib catalog is baked into a `template.db` at build time, shipped in the
app bundle; creating a database is a file copy. This removes what would
otherwise be the worst problem for a local-first database — `_init_stdlib`
writes thousands of catalog rows, which no app can pay on first launch — and
puts the whole of bootstrap on the build-time side of the phase-2 line.

### Accepted downside

SQLite rebuilds are O(table size) and need roughly 2x the table's disk free. On
a device with a large local table, a schema migration is a full copy in time and
space. This is inherent to SQLite and must be documented for app authors rather
than hidden.

---

## 7. Execution layer

`sqlitecon` implements the interface `dbview`/`protocol` actually use:
`sql_execute`, `sql_fetch`, `sql_fetch_val`, `sql_fetch_col`, `sql_describe`,
`parse_execute`, plus lifecycle (`close`, `terminate`, `abort`, `is_healthy`).
Ten methods. That narrowness is what makes phase 2 credible.

### Connection preamble

Applied on every connection open, before any transaction begins:

```
PRAGMA foreign_keys = ON;      -- off by default; no-op inside a transaction
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;   -- WAL-safe; FULL only guards power loss mid-commit
PRAGMA busy_timeout = 5000;
PRAGMA trusted_schema = OFF;
```

Then UDFs, collations, and virtual tables are registered.

`foreign_keys = ON` is mandatory and non-negotiable: SQLite ships FK enforcement
off for backwards compatibility, and it cannot be enabled once a transaction has
begun. The only exception is the Section 6 rebuild window, which disables it
outside the transaction and runs `PRAGMA foreign_key_check` before restoring.

`trusted_schema = OFF` is a security decision. A SQLite file's schema can carry
views, triggers, and generated columns that invoke functions; since gelite
registers ~40 UDFs on every connection, opening an untrusted `.db` would
otherwise give that file's schema a way to call them.

### Transactions

Gel drives `BEGIN`/`COMMIT` itself, and `QueryUnit` models savepoints
(`tx_savepoint_declare`, `tx_savepoint_rollback`), which map directly onto
SQLite's `SAVEPOINT`/`RELEASE`/`ROLLBACK TO`. This is the second reason for apsw:
stdlib `sqlite3` performs implicit transaction management that would fight this.

**Any transaction that might write uses `BEGIN IMMEDIATE`.** A deferred
transaction that later upgrades to a write can hit `SQLITE_BUSY_SNAPSHOT`, which
is unrecoverable mid-transaction and would surface as an intermittent failure.
`QueryUnit.capabilities` already records whether a unit modifies data, so the
choice is derivable at compile time.

### Async and cancellation

SQLite is synchronous and in-process. The `async def` signatures stay so
`dbview`/`protocol` are untouched, but nothing awaits I/O. A long query blocks
the event loop, which is acceptable for a single-user local database — except
that it would break query cancellation. apsw's progress handler is the mechanism
for interruption, and is the only part of the async story needing real design.

### Concurrency

One write connection, optionally N read connections, under WAL. Gel's model
assumed a pool; that assumption is removed.

---

## 8. Testing

### Differential testing against Postgres is the core technique

This is a fork of a working implementation, so identical EdgeQL can be run
against gelite and against **stock upstream Gel, pinned, in a container** — code
that is never touched, never merged, never fixed, and (upstream being dead) will
never drift.

This is the only approach that reliably catches this design's characteristic
failure mode. Four silent-wrongness classes are already identified:

1. Lax casts returning `0`/empty instead of raising (Section 5).
2. Integer overflow promoting to `REAL` instead of raising (Section 5).
3. JSONB key-order equality and absent total ordering (Section 2).
4. Constraint-check atomicity under script splitting (Section 4).

Each produces a plausible wrong answer rather than an error, and none is
reliably caught by assertions written against expected values.

### The oracle is permanent infrastructure

**The Postgres oracle container is kept indefinitely.** It is not retired after
the suite goes green.

The reasoning: the container is stock upstream Gel, pinned — code this project
never touches, never merges, and never fixes. Upstream being inactive, it will
never drift. So its carrying cost is CI minutes, not maintenance, and there is no
pressure to delete it.

Against that, its value does not end at green. Once the suite passes it *is* the
oracle for everything it covers (229k lines of encoded expected behavior), but
what it covers is fixed, and every one of the four silent-wrongness classes lives
in the gaps. A permanent oracle means generative differential testing stays
available for as long as the project runs — for new features, for regressions in
areas the suite is thin on, and for adjudicating "is this a bug or is this
Gel's actual behavior?" questions that would otherwise need archaeology.

Deleting it would be a one-way door bought for nearly no savings.

**Operationally:** build the image once and archive it. The usual thing that
forces deletion of a frozen reference container is that it stops building against
dead dependencies years later; archiving the built image defuses that, and is the
one piece of upkeep this decision requires.

The release gate is unchanged: **all non-deferred tests green**, with an explicit
xfail list for ranges, `decimal`/`bigint`, and FTS — that list is the ledger of
what v2 owes.

### Other layers

- Section 3's normalization passes get pure tree-in/tree-out unit tests, no
  database.
- Casts and arithmetic get a dedicated sweep, separate from feature tests.
- `tests/` is kept wholesale, with xfail markers making deferred scope visible
  rather than deleted.

---

## 9. Phase-2 seam

| Ports to Rust in phase 2 | Stays Python (build-time) |
|---|---|
| The ~10-method executor (apsw -> rusqlite) | Parser, IR, EdgeQL compiler |
| ~40 UDFs, collations, `generate_series` vtable | The `pgast` normalization passes |
| Type-descriptor decode — the `gel-protocol` crate is already a workspace dependency | `delta.py` + `dbops` migrations |
| Temp-table schemas (serialized into the `QueryUnit`) | Bootstrap, baked into `template.db` |

Essentially all 260k lines live in the right-hand column. That is why phase 2 is
an extraction rather than a rewrite.

---

## 10. Risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Constraint-check atomicity lost by script splitting (Section 4) | **High** | Milestone 2 probe runs concurrently with the DML work, which accepts rework risk for schedule. `defer_foreign_keys` covers FKs only. |
| 2 | Silent wrong answers from lax casts and overflow (Section 5) | **High** | 52 explicit checked casts; dedicated sweep; differential oracle. |
| 3 | JSONB semantics differ from PG `jsonb` (Section 2) | Medium | Canonicalize on write; comparison UDFs. |
| 4 | Residual laterals more common than expected (Section 3) | Medium | Measured in Milestone 1, before the design depends on it. |
| 5 | `UNLESS CONFLICT` machinery (Section 4) | Medium | Its own workstream; not assumed covered by upsert syntax. |
| 6 | Deep inheritance hierarchies hit SQLite's weaker `UNION ALL` planning | Medium | Flatten or materialize `inhview`s if measurement demands. |
| 7 | Migration rebuilds are O(table size) and need 2x disk | Low (inherent) | Document for app authors. |

---

## 11. Milestones

Ordered so that the two highest-severity unknowns are resolved before the work
that depends on them.

**Milestone 0 — Demolition.** Execute Section 1: fork the package, invert the
four frontend leaks, delete the listed subsystems. No SQLite code. Ends with a
tree that builds and whose remaining Postgres dependencies are all in
`edb/sqlite/`.

**Milestone 1 — Vertical slice (read path).** One object type, one link, one
multi-property, `SELECT` only, end to end through the real server against a
SQLite file. Validates the Section 2 type mapping and the Section 3 lateral
decorrelation. **Measures how often residual laterals occur** (risk 4).

**Milestone 2 — Constraint-atomicity probe. Runs alongside Milestone 3.**
Determine empirically where Gel relies on single-statement constraint-check
atomicity: link table delete-then-reinsert, exclusive constraints across
rewrites, multi-statement policy enforcement. Output is a decision, not code —
either the exposure is bounded and materialize-then-mutate proceeds as designed,
or it is not and Section 4 needs revision.

This does not block Milestone 3. The two run concurrently, which trades a risk
of rework for schedule: if the probe reports badly, some already-written DML
lowering is discarded. That trade is deliberate, and it holds only while the
probe stays genuinely concurrent — **if Milestone 3 reaches the link-table and
exclusive-constraint work before the probe reports, the probe becomes the
blocker after all**, because that is precisely the code whose correctness the
probe determines. Sequence Milestone 3 to do the straightforward INSERT and
DELETE paths first so the probe has time to land.

**Milestone 3 — DML and overlays.** Section 4. Ordered so that the work most
sensitive to Milestone 2's finding comes last.

**Milestone 4 — Stdlib.** Section 5, with the cast/overflow sweep built
alongside rather than after.

**Milestone 5 — DDL, migrations, and `template.db`.** Section 6.

**Milestone 6 — Green.** Non-deferred suite passing, xfail ledger published.

**Milestone 7 — Generative differential pass.** The Postgres oracle stays in
CI permanently after this; see Section 8.

Phase 2 (thin native runtime) is out of scope for this design and gets its own
spec.

---

## 12. Open questions

1. **Milestone 2's outcome is genuinely unknown.** If Gel depends heavily on
   single-statement constraint atomicity, Section 4 needs revision. The design
   deliberately does not guess.
2. **Residual lateral frequency** (risk 4) is an assumption to be measured in
   Milestone 1, not a finding.
3. **Cancellation design** via apsw's progress handler is sketched, not
   specified.
4. **Whether `__version` should be per-row or per-table** for the sync
   affordance — per-row is assumed; not yet validated against any sync design,
   since none exists.
