---
name: docstring-tests
description: Handle the test files that encode test data in docstrings, where reformatting silently deletes assertions. Use before editing or formatting any file in the ruff-format exclusion list.
---

# Docstring test data

Eight test files carry their **test data**, not just documentation, in
docstrings. Reformatting them deletes assertions without failing
anything.

```
tests/test_edgeql_ir_card_inference.py
tests/test_edgeql_ir_mult_inference.py
tests/test_edgeql_ir_type_inference.py
tests/test_edgeql_ir_volatility_inference.py
tests/test_edgeql_syntax.py
tests/test_schema.py
tests/test_schema_syntax.py
tests/test_sql_parse.py
```

They are listed under `[tool.ruff.format] exclude` in `pyproject.toml`,
with the reasoning inline. Keep them excluded.

## The mechanism

`DocTestMeta` in `edb/testbase/lang.py:104` splits each test's docstring:

```python
source, _, output = doc.partition('\n% OK %')
```

The marker must be a newline followed by `% OK %` **at column 0**.
`ruff format` re-indents docstring bodies to match the enclosing block,
moving the marker to column 8, where it no longer matches.

The tests then do not fail. `partition` finds nothing, `expected` becomes
`None`, and each test quietly degrades into "parse this blob and assert
nothing". Measured across these files, formatting drops **520 of 521**
expected-output assertions and leaves the suite green.

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
