---
name: dev-environment
description: Set up a working checkout of this repo, and know what cannot be done locally. Use when starting fresh, after a container restart, or when an import or build fails in a way the code does not explain.
---

# Development environment

## Dependencies only (enough for lint, typecheck, and frontend tests)

Use CI's own `uv sync` commands, so what you run matches what CI runs:

| purpose | command |
|---|---|
| ruff | `uv sync --inexact --only-group lint --no-install-project` |
| mypy | `uv sync --inexact --no-install-project --extra test` |
| build/test deps | `uv sync --active --inexact --no-install-project --extra test --extra language-server --group build` |

`--no-install-project` is what keeps a sync off the Cython and Rust
build; it is why the lint job takes seconds.

**`--inexact` is a local addition.** CI omits it because every job starts
from a fresh venv. Run CI's lint sync verbatim in a shared local venv and
it will *prune* mypy back out; `--inexact` leaves unrelated packages
alone.

This is enough to run everything in the `running-checks` skill and the
no-server tests in `running-tests`.

## A full build (needed for anything that starts a server)

Two system packages are missing from a plain container, and neither is in
CI's apt line, so CI is not a guide here:

```
apt-get install -y libicu-dev flex
```

Then build and install the project into the venv:

```
uv sync --active --inexact --no-build-isolation \
  --extra test --extra language-server --group build
```

That is CI's post-build sync (`--no-build-isolation` where the
dependencies-only syncs use `--no-install-project`). Expect **several
minutes**; the Cython and Rust extensions dominate.

The project must genuinely be *installed*, not merely on `PYTHONPATH`:
the test cluster spawns `python -I -m edb.server.main`, and `-I`
(isolated mode) ignores `PYTHONPATH` entirely. A `PYTHONPATH` that looks
right will still fail.

## What you cannot do as root

`pg_ctl` refuses to run as root, so a test that starts a PostgreSQL
cluster cannot run in a container whose user is root — the build
succeeds and the cluster then fails to start. Chowning the tree to a
non-root user is possible but expensive (~1.6 GB).

Practical consequence: **server-backed tests are verified in CI, not
locally.** Say so plainly rather than implying local verification that
did not happen. The frontend suites listed in `running-tests` still run
locally and are worth using.

## `__EDGEDB_DEVMODE=1`

Set it for anything that imports `edb.buildmeta` from a source checkout.
Without it, `get_version()` looks for generated distribution metadata
that an editable install lacks and raises

```
MetadataError: could not find VERSION in Gel distribution metadata
```

With it, the version comes from git. `edb/common/devmode.py` reads the
variable; any value but `0`, empty or `false` counts as on.

## Sanity checks

```
uv run --no-sync ruff --version     # 0.11.2
uv run --no-sync mypy --version     # 1.13.0
uv run --no-sync python -c "import edb; print(edb.__file__)"
__EDGEDB_DEVMODE=1 uv run --no-sync edb test tests/test_sourcecode.py
```

The last one is the cheapest end-to-end proof that the runner works.

## Stale build artifacts lie

Compiled extensions are untracked and outlive the sources they came
from, so a working tree can fail in ways a fresh CI checkout never
would. After moving or renaming a `.pyx`, check what the binaries
actually contain:

```
for so in $(find edb -name '*.so'); do
  strings "$so" | grep -q 'edb\.pgsql' && echo "STALE: $so"
done
```

**A `git mv` preserves mtime**, so a generated `.c` sitting next to a
moved `.pyx` is now *newer* than its source and Cython skips
regenerating it. The stale `.c` bakes the old dotted module name into
the build, and the extension registers itself under a name that no
longer exists. Rebuilding does not fix it — delete the generated `.c`
first:

```
rm -f edb/<pkg>/<mod>.c
BUILD_EXT_MODE=py-only uv run --no-sync python setup.py build_ext --inplace
```

Those `.c` files are gitignored, so a fresh checkout regenerates them and
CI never sees this. It is a local-tree problem that looks like a code
problem.

## After a container restart

A restarted container keeps the git checkout but loses the built
artifacts and often the venv. Re-run the dependency sync above. Do not
commit anything that reappears under `edb/` as build residue — generated
files there have previously been mistaken for source and nearly
resurrected deleted code.
