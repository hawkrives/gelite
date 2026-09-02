#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2016-present MagicStack Inc. and the EdgeDB authors.
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

import json

from edb.testbase import server as tb


class TestBranching(tb.QueryTestCase):
    """Characterisation tests for schema and data branches.

    Branching already works on the Postgres backend; these tests exist to
    pin that behaviour down before the SQLite port changes how branches
    are created. Section 6 of the design makes a branch a separate SQLite
    file copied from a template, which is a new implementation of an
    existing guarantee.

    They replace coverage lost with check_branching, which was deleted
    along with the dump/restore test scaffolding it happened to live in.

    tests/test_database.py already covers branch *lifecycle* - create,
    drop, rename, alias, dropping while connected - but every one of its
    cases uses CREATE EMPTY BRANCH. Nothing else in the suite creates a
    copying branch successfully: the only executing CREATE DATA BRANCH
    asserts that it raises for a missing template, and the SCHEMA BRANCH
    occurrences in test_edgeql_syntax.py are parser round-trips. Branch
    *fidelity* is what is untested, and what these cover.
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

        await self.con.execute(f'create {branch_type} branch {new} from {orig}')
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
