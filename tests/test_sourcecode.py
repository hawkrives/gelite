#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2017-present MagicStack Inc. and the EdgeDB authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import ast
import os
import pathlib
import unittest


# The below files must not have any Python code in them;
# there should be a comment in each of them explaining why.
EMPTY_INIT_FILES = {
    'edb/__init__.py',
    'edb/common/__init__.py',
    'edb/sqlite/__init__.py',
    'edb/tools/__init__.py',
}


# Nothing under these may import the backend, so that the backend can be
# forked and replaced without touching them. The last three are the
# frontend proper; edb/common is shared infrastructure that sits below
# both and so has even less business naming a backend.
BACKEND_FREE_DIRS = ('edb/common', 'edb/edgeql', 'edb/ir', 'edb/schema')

# The backend package the frontend must not name. `edb/schema/backend.py`
# is the seam that replaced the imports this forbids: the backend registers
# itself there on import, rather than the frontend reaching for it.
BACKEND_PACKAGE = 'edb.sqlite'

# Raw SQL as an input language is gone (#88): it existed to serve Postgres
# clients over the wire, and #34 removed the protocol they arrive on. These
# packages carried it. Nothing may import them again -- reinstating SQL
# input is a design decision, not something a stray import should smuggle
# back in.
DELETED_SQL_PACKAGES = (
    'edb.sqlite.parser',
    'edb.sqlite.resolver',
    'edb.server.compiler.sql',
)


def find_edgedb_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestCodeQuality(unittest.TestCase):
    def test_cqa_empty_init(self):
        edgepath = find_edgedb_root()
        for sn in EMPTY_INIT_FILES:
            fn = os.path.join(edgepath, sn)
            if not os.path.exists(fn):
                self.fail(f'not found an empty __init__.py file at {fn}')

            with open(fn, 'rt') as f:
                for line in f:
                    if line.startswith('#') or not line.strip():
                        continue

                    self.fail(
                        f'{fn} must be an empty file (except Python comments)'
                    )

    def test_cqa_frontend_does_not_import_backend(self):
        # An AST walk rather than a grep: the imports this replaced were
        # deferred ones inside function bodies, which is exactly what a
        # module-scope check would miss, and `edb/schema/backend.py` names
        # the backend in prose that a grep would flag.
        root = pathlib.Path(find_edgedb_root())
        prefix = BACKEND_PACKAGE + '.'
        offenders = []

        for d in BACKEND_FREE_DIRS:
            for path in sorted((root / d).rglob('*.py')):
                tree = ast.parse(path.read_text(), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        # level > 0 is a relative import, which cannot
                        # reach outside the frontend package it sits in.
                        names = [node.module] if node.level == 0 else []
                    else:
                        continue

                    for name in names:
                        if name == BACKEND_PACKAGE or (
                            name is not None and name.startswith(prefix)
                        ):
                            rel = path.relative_to(root)
                            offenders.append(f'{rel}:{node.lineno}: {name}')

        if offenders:
            listing = '\n  '.join(offenders)
            self.fail(
                f'the frontend must not import {BACKEND_PACKAGE}, but '
                f'these do:\n  {listing}\n'
                f'Add a hook to edb/schema/backend.py and register it from '
                f'the backend instead.'
            )

    def test_cqa_no_sql_input_language(self):
        # A grep would flag prose and the surviving `sql` attribute on
        # QueryUnit, so walk the imports instead -- both module-scope and
        # the deferred ones inside function bodies, which is how
        # `edb/server/compiler/sql.py` reached the resolver.
        root = pathlib.Path(find_edgedb_root())
        offenders = []

        for path in sorted(root.glob('edb/**/*.py')):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0:
                        if node.module is None:
                            continue
                        base = node.module
                    else:
                        # A relative import resolves against the importing
                        # module's package. `edb/sqlite/metaschema.py` said
                        # `from .resolver import sql_introspection`, which an
                        # absolute-only check does not see -- and that is
                        # exactly how the deleted package survived the first
                        # sweep of #88.
                        pkg = path.relative_to(root).parent.parts
                        if node.level > 1:
                            pkg = pkg[: -(node.level - 1)]
                        base = '.'.join(pkg)
                        if node.module:
                            base = f'{base}.{node.module}'
                    # `from edb.sqlite import parser` names the package in
                    # the alias, not the module.
                    names = [base] + [f'{base}.{a.name}' for a in node.names]
                else:
                    continue

                for name in names:
                    for pkg in DELETED_SQL_PACKAGES:
                        if name == pkg or name.startswith(pkg + '.'):
                            rel = path.relative_to(root)
                            offenders.append(f'{rel}:{node.lineno}: {name}')

        if offenders:
            listing = '\n  '.join(offenders)
            self.fail(
                f'raw SQL input was removed in #88, but these import it '
                f'again:\n  {listing}'
            )
