---
name: running-tests
description: Run this repo's test suite. It is not pytest, and the obvious invocation silently passes without running anything. Use before pushing, when reproducing a CI shard failure, or when adding a test.
---

# Running tests

## The runner

Not pytest. The runner is the `edb` console script
(`edb = "edb.tools.edb:edbcommands"` in `pyproject.toml`):

```
uv run --no-sync edb test tests/test_schema.py -k test_schema_range_01
```

**Never use `python -m edb.tools.edb test`.** The module has no
`__main__` guard, so it produces zero bytes and **exits 0** — a false
pass indistinguishable from a real one in any exit-code check. This has
already been reported as a success once when nothing had run.

Useful flags (`edb test --help` for the rest):

| flag | use |
|---|---|
| `-j, --jobs N` | parallelism; `0` (default) picks by CPU count |
| `-k, --include REGEXP` | run only matching tests |
| `-e, --exclude REGEXP` | skip matching tests |
| `-x, --failfast` | stop at the first failure |
| `-s, --shard C/T` | reproduce one CI shard exactly |
| `--list` | list tests without running them |

To reproduce a CI shard failure, match CI's own invocation:

```
uv run --no-sync edb test --jobs 4 --verbose --shard 14/16
```

## Check the cost before you start

`.github/time_stats.csv` holds the per-test times CI measured. Consult it
**before** launching a suite, or you will start something far longer than
you think:

```
python3 -c "
import csv, sys
p = sys.argv[1]
t = sum(float(r[1]) for r in csv.reader(open('.github/time_stats.csv'))
        if p in r[0])
print(f'{t/60:.0f} min single-threaded')
" tests.test_schema
```

`tests/test_schema.py` alone is **584 tests, ~92 minutes** single-threaded.
The whole suite is ~8 hours single-threaded, which is why CI splits it
16 ways. Use `-j` and `-k`; a bare `python -m unittest` on a big module
has no parallelism at all and will run for hours.

## Which tests need a running server

**No server needed** — these run anywhere, in seconds to minutes:

- `tests/test_sourcecode.py`
- `tests/test_schema.py`, `tests/test_schema_syntax.py`
- `tests/test_edgeql_syntax.py`, `tests/test_edgeql_ir_*.py`

They build schemas in-process through `edb.testbase.lang`. They need
**`__EDGEDB_DEVMODE=1`**:

```
__EDGEDB_DEVMODE=1 uv run --no-sync edb test tests/test_schema.py -k range
```

Without it `buildmeta.get_version()` looks for generated distribution
metadata that an editable install does not have, and every test dies with

```
MetadataError: could not find VERSION in Gel distribution metadata
```

which looks like a code failure and is not. Dev mode makes it read the
version from git instead.

**Server needed** — `tests/test_edgeql_ddl.py`, `test_database.py`,
`test_branching.py`, and most of the rest. These spawn a real PostgreSQL
cluster. See the `dev-environment` skill for what that requires, and note
that it cannot be done as root: `pg_ctl` refuses to run as root, so in a
container running as root these can only be verified in CI.

## A local pass is weaker evidence than it looks

A working checkout has the project installed and the extensions built.
CI's `build` job has **dependencies installed but the project not built**,
and runs `setup.py` against that. Code that imports fine locally can fail
there — this is how one push went red after ruff, mypy and the unit tests
were all clean locally.

If a change touches `edb/sqlite/__init__.py`, `edb/buildmeta.py`, `setup.py`
or anything they import at module scope, check that path directly:

```
uv run --no-sync python -c "
import sys, importlib.util
importlib.util.find_spec('edb.sqlite.metaschema')
assert 'edb._edgeql_parser' not in sys.modules
print('build path clean')
"
for t in rust ext parsers postgres libpg_query bootstrap build_lib build_temp; do
  uv run --no-sync python setup.py -q ci_helper --type $t >/dev/null || echo "FAILED: $t"
done
```
