#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2019-present MagicStack Inc. and the EdgeDB authors.
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


import edgedb

from edb.sqlite import common

from edb.testbase import server as tb
from edb.testbase import connection as tb_connection


class TestQueryStatsMixin:
    stats_magic_word: str = NotImplemented
    stats_type: str = NotImplemented
    counter: int = 0
    con: tb_connection.Connection

    async def _query_for_stats(self):
        raise NotImplementedError

    async def _configure_track(self, option: str):
        raise NotImplementedError

    async def _bad_query_for_stats(self):
        raise NotImplementedError

    def _before_test_sys_query_stats(self):
        if self.backend_dsn:
            self.skipTest(
                "can't run query stats test when extension isn't present"
            )

    def make_stats_query(self, tag: str | None = None) -> str:
        tag_filter = ''
        if tag is not None:
            tag_filter = f'and .tag = {common.quote_literal(tag)}'
        return f'''
            with stats := (
                select
                    sys::QueryStats
                filter
                    .query like '%{self.stats_magic_word}%'
                    and .query not like '%sys::%'
                    and .query_type = <sys::QueryType>$0
                    {tag_filter}
            )
            select sum(stats.calls)
        '''

    async def _test_sys_query_stats(self):
        stats_query = self.make_stats_query()

        # Take the initial tracking number of executions
        calls = await self.con.query_single(stats_query, self.stats_type)

        # Execute the query one more time
        await self._query_for_stats()
        self.assertEqual(
            await self.con.query_single(stats_query, self.stats_type),
            calls + 1,
        )

        # Bad queries are not tracked
        await self._bad_query_for_stats()
        self.assertEqual(
            await self.con.query_single(stats_query, self.stats_type),
            calls + 1,
        )

        # sys::reset_query_stats() branch filter works correctly
        self.assertIsNone(
            await self.con.query_single(
                "select sys::reset_query_stats(branch_name := 'non_exdb')"
            )
        )
        self.assertEqual(
            await self.con.query_single(stats_query, self.stats_type),
            calls + 1,
        )

        # sys::reset_query_stats() works correctly
        self.assertIsNotNone(
            await self.con.query('select sys::reset_query_stats()')
        )
        self.assertEqual(
            await self.con.query_single(stats_query, self.stats_type),
            0,
        )

        # Turn off cfg::Config.track_query_stats, verify tracking is stopped
        await self._configure_track('None')
        await self._query_for_stats()
        await self._query_for_stats()
        self.assertEqual(
            await self.con.query_single(stats_query, self.stats_type),
            0,
        )

        # Turn cfg::Config.track_query_stats back on again
        await self._configure_track('All')
        await self._query_for_stats()
        self.assertEqual(
            await self.con.query_single(stats_query, self.stats_type),
            1,
        )

    async def _test_sys_query_stats_with_tag(self):
        # Test tags are correctly set
        tag = 'test_tag'
        self.con = self.con.with_query_tag(tag)
        self.stats_magic_word += "Tagged"
        self.assertEqual(
            await self.con.query_single(
                self.make_stats_query(tag=tag), self.stats_type
            ),
            0,
        )
        await self._query_for_stats()
        self.assertEqual(
            await self.con.query_single(
                self.make_stats_query(tag=tag), self.stats_type
            ),
            1,
        )


class TestEdgeQLSys(tb.QueryTestCase, TestQueryStatsMixin):
    stats_magic_word = 'TestEdgeQLSys'
    stats_type = 'EdgeQL'

    async def test_edgeql_sys_locks(self):
        lock_key = tb.gen_lock_key()

        async with self.assertRaisesRegexTx(
            edgedb.InternalServerError,
            "lock key cannot be negative",
        ):
            await self.con.execute('select sys::_advisory_lock(-1)')

        async with self.assertRaisesRegexTx(
            edgedb.InternalServerError,
            "lock key cannot be negative",
        ):
            await self.con.execute('select sys::_advisory_unlock(-1)')

        self.assertEqual(
            await self.con.query(
                'select sys::_advisory_unlock(<int64>$0)', lock_key
            ),
            [False],
        )

        await self.con.query('select sys::_advisory_lock(<int64>$0)', lock_key)

        self.assertEqual(
            await self.con.query(
                'select sys::_advisory_unlock(<int64>$0)', lock_key
            ),
            [True],
        )
        self.assertEqual(
            await self.con.query(
                'select sys::_advisory_unlock(<int64>$0)', lock_key
            ),
            [False],
        )

    async def _query_for_stats(self):
        self.counter += 1
        self.assertEqual(
            await self.con.query(
                f'select ('
                f'{self.stats_magic_word}{self.counter} := {self.counter})'
            ),
            [(self.counter,)],
        )

    async def _configure_track(self, option: str):
        await self.con.query(f'''
            configure session set track_query_stats :=
                <cfg::QueryStatsOption>{common.quote_literal(option)};
        ''')

    async def _bad_query_for_stats(self):
        async with self.assertRaisesRegexTx(
            edgedb.InvalidReferenceError, 'does not exist'
        ):
            await self.con.query(f'select {self.stats_magic_word}_NoSuchType')

    async def test_edgeql_sys_query_stats(self):
        self._before_test_sys_query_stats()
        async with tb.start_edgedb_server() as sd:
            old_con = self.con
            self.con = await sd.connect()
            try:
                await self._test_sys_query_stats()
                await self._test_sys_query_stats_with_tag()
            finally:
                await self.con.aclose()
                self.con = old_con
