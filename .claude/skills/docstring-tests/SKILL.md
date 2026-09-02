---
name: docstring-tests
description: Handle the test files that encode test data in docstrings, where reformatting silently deletes assertions. Use before editing or formatting any file in the ruff-format exclusion list.
---

# Docstring test data

Three test files carry their **test data**, not just documentation, in
docstrings, and must never be reformatted:

```
tests/test_edgeql_syntax.py
tests/test_schema.py
tests/test_schema_syntax.py
```

They are listed under `[tool.ruff.format] exclude` in `pyproject.toml`,
with the reasoning inline. Keep them excluded.

Five more used to be on that list and no longer are: the marker-matching
bug below was fixed in #67, which is what made them safe to format. The
three above are excluded for a stronger reason - their docstrings contain
whitespace that *is* the assertion.

## The mechanism, and what #67 changed

`DocTestMeta` in `edb/testbase/lang.py` splits each test's docstring on a
`% OK %` (or `% ERROR %`) marker line. It used to do that with a literal
`doc.partition('\n% OK %')`, which required the marker at **column 0**.
`ruff format` re-indents docstring bodies, moving it to column 8.

The tests then did not fail. `partition` found nothing, `expected` became
`None`, and each test quietly degraded into "parse this blob and assert
nothing" — **520 of 521** expected-output assertions dropped, suite still
green.

The split now uses `^[ \t]*% OK %` in multiline mode, so indentation no
longer matters. Note the deliberate absence of a trailing anchor: two
tests end their docstring as `% OK %  """`, and anchoring to end-of-line
swallows those spaces, making `output` empty — which the `if not output`
fallthrough reads as "no marker" and parses the whole docstring, marker
included, as source.

The harness does **not** dedent, and should not start without a decision:
110 `col=` assertions in these files sit on multi-line docstrings and
measure columns that include the indentation.

`tests/test_schema.py` is the same hazard by another route: its
docstrings are SDL source that `BaseSchemaLoadTest.load_schema`
interpolates verbatim into `module default { ... }` with no dedent.
Re-indenting shifts every column, and 23 `must_fail` decorators there
assert an exact `col=`. Those fail loudly rather than quietly, but the
"fix" would be renumbering assertions to suit a formatter.

## Rules

- **Never** run `ruff format` on these files, and never remove them from
  the exclusion list without first making the harness tolerate
  indentation (issue #67).
- When editing one, keep `% OK %` at column 0 even though it looks
  misaligned inside an indented docstring. That is correct.
- `ruff check` is fine on them; only the formatter is excluded.
- Adding a new file that encodes data in docstrings? Add it to the
  exclusion list in the same commit.

## Verifying you have not broken one

Assertion loss is silent, so a green run proves nothing. Count the
markers before and after:

```
grep -c '^% OK %' tests/test_edgeql_ir_card_inference.py
```

The count must not change. A drop to zero with the suite still passing is
exactly the failure this skill exists to prevent.
