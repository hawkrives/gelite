---
name: deleting-a-subsystem
description: Remove a subsystem from this fork without silently taking working code or test coverage with it. Use for any Milestone 0 demolition task, or any change that deletes more than a file or two.
---

# Deleting a subsystem

This fork's Milestone 0 is demolition — tens of thousands of lines
removed. Green CI is a weak signal for a deletion: the tests that would
have objected are often the ones deleted alongside.

## Before deleting

1. **Read the design.** `docs/superpowers/specs/2026-08-30-gel-on-sqlite-design.md`
   §1 lists what goes and what is merely excluded from the build. Its
   inventory is a starting point, not gospel — it has mislabelled call
   sites before. Verify each claim against the tree.
2. **Find every reference**, not just imports: string literals, config
   keys, workflow steps, `pyproject.toml` entries, `Cargo.toml` workspace
   members, `edb/lib/*.edgeql`.
3. **Ask what a caller would lose.** If a caller only wants the deleted
   code for error quality or validation, deleting it changes behaviour
   even when nothing fails.

## The trap: coverage that lives next to the code

Test *data* often sits adjacent to the tests being removed. Deleting a
test can take a fixture that a surviving test still needs — `SCHEMA_gcache`
was swallowed exactly this way, by a deletion of the test beside it, and
nothing went red.

Audit by structure, not by eye:

```
git show <before>:tests/test_x.py > /tmp/before.py
python3 - <<'PY'
import ast
def names(p):
    t = ast.parse(open(p).read())
    return {n.id for x in ast.walk(t) if isinstance(x, ast.Assign)
            for n in x.targets if isinstance(n, ast.Name)}
print('lost:', sorted(names('/tmp/before.py') - names('tests/test_x.py')))
PY
```

Every name in `lost` must be either genuinely dead or deliberately moved.

## Behaviour that only one thing enforces

Before removing a check, work out what it *uniquely* rejects. Other
checks nearby often cover most cases, leaving one that only the doomed
code catches — and that one is usually untested.

Write a characterisation test for it **first**, and prove it passes both
with your change stashed and applied:

```
git stash push -- edb
uv run --no-sync edb test tests/test_x.py -k the_new_test   # must pass
git stash pop
uv run --no-sync edb test tests/test_x.py -k the_new_test   # must still pass
```

`git stash push -- <path>` is a no-op on a clean tree, so commit or stage
nothing first and check that the stash actually took.

## Regenerate what the deletion invalidates

- **`Cargo.lock`** — deleting a crate or a workspace member leaves stale
  entries. CI now catches this (`cargo metadata --locked` in
  `rust-rustfmt`), but regenerate rather than waiting for red.
- **`uv.lock`** — same, via `uv lock`; checked by `uv lock --check` in
  `python-lint`.
- Never hand-edit either.

## Add the guard the deletion needs

If the point was to establish a boundary, encode it. `tests/test_sourcecode.py`
holds static checks that need no server —
`test_cqa_frontend_does_not_import_backend` is an AST walk that fails the
build if the frontend imports the backend again. Prove a new guard fails
on the pre-change tree before trusting it.

## Before pushing

- All three checks from the `running-checks` skill.
- The no-server suites from `running-tests`.
- A deliberate look for build residue: a deletion plus a stale `.so`,
  `.pyc` or generated file can resurrect deleted code. Never commit
  regenerated files under a directory you just emptied.
