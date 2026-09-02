---
name: docstring-tests
description: Handle the test files that encode test data in docstrings, where the formatter can rewrite an assertion out from under you. Use before editing a docstring in tests/test_edgeql_syntax.py, test_schema.py, test_schema_syntax.py, test_sql_parse.py or the test_edgeql_ir_*_inference.py files.
---

# Docstring test data

Eight test files carry their **test data**, not just documentation, in
docstrings:

```
tests/test_edgeql_syntax.py                   tests/test_schema.py
tests/test_schema_syntax.py                   tests/test_sql_parse.py
tests/test_edgeql_ir_card_inference.py        tests/test_edgeql_ir_mult_inference.py
tests/test_edgeql_ir_type_inference.py        tests/test_edgeql_ir_volatility_inference.py
```

`ruff format` rewrites docstring bodies. In these files that can change
what is being asserted.

**None of them is excluded from the formatter any more** — that was the
old arrangement, and it meant nothing in those files got formatted. #67
replaced it with two narrower mechanisms.

## 1. The marker tolerates indentation

`DocTestMeta` in `edb/testbase/lang.py` splits each test's docstring on a
`% OK %` (or `% ERROR %`) marker line. It used to use a literal
`doc.partition('\n% OK %')`, which required the marker at **column 0**.
Re-indenting moved it, `partition` found nothing, `expected` became
`None`, and the test quietly degraded into "parse this blob and assert
nothing" — **520 of 521** expected-output assertions dropped, suite still
green.

The split now matches `^[ \t]*% OK %` in multiline mode. Note the
deliberate absence of a trailing anchor: two tests end their docstring as
`% OK %  """`, and anchoring to end-of-line swallows those spaces, making
`output` empty — which the `if not output` fallthrough reads as "no
marker" and parses the whole docstring, marker included, as source.

The harness does **not** dedent, and should not start without a decision:
110 `col=` assertions sit on multi-line docstrings and measure columns
that include the indentation.

## 2. Whitespace-significant docstrings carry `# fmt: skip`

265 docstrings across the three syntax/schema files have indentation that
*is* the test data — tab-indented source that exercises the lexer, or
columns a `col=` assertion measures. Each is marked:

```python
def test_eschema_syntax_type_08(self):
    """
        module test {
            type Foo {
    ...
    """  # fmt: skip
```

**Placement matters.** Verified against ruff 0.11.2:

| marker | protects the docstring? |
|---|---|
| `# fmt: skip` after the closing `"""` | **yes** |
| `# fmt: off` / `# fmt: on` around the `def` | yes, but suppresses the code too |
| `# fmt: skip` on the `def` line | **no** |

## Rules

- Adding a docstring whose indentation matters? Put `# fmt: skip` after
  its closing quotes, in the same commit.
- Keep `% OK %` wherever it is. Indentation no longer breaks it.
- `ruff check` was always fine on these files; only the formatter was ever
  the hazard.

## Verifying you have not broken one

Assertion loss used to be silent, so a green run proved nothing. Count the
markers before and after any change to these files or to the harness — it
must be **521**:

```
uv run --no-sync python - <<'PY'
import ast, re, glob
M = re.compile(r'^[ \t]*% (OK|ERROR) %', re.M)
n = 0
for f in glob.glob('tests/test_edgeql_syntax.py') + glob.glob(
        'tests/test_schema_syntax.py') + glob.glob(
        'tests/test_sql_parse.py') + glob.glob(
        'tests/test_edgeql_ir_*_inference.py'):
    for node in ast.walk(ast.parse(open(f).read())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d and M.search(d):
                n += 1
print(n)
PY
```

A drop with the suite still passing is exactly the failure this skill
exists to prevent.

## Cost of running these

`tests/test_schema.py` is **584 tests, ~92 minutes single-threaded** — use
`-j 4` and `-k`, and check `.github/time_stats.csv` before starting
anything else here.
