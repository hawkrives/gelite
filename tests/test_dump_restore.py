#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2026-present MagicStack Inc. and the EdgeDB authors.
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

"""Dump/restore round-trip coverage (#26).

Nothing has exercised dump or restore since #2 deleted the suites that
drove them through the `gel` CLI. That CLI is not coming back, and
CLITestCaseMixin went with those tests, so this drives the binary
protocol directly instead - the same messages the CLI would have sent.

`edb/protocol/messages.py` models the whole exchange. Restore wants "the
original DumpHeader packet data excluding mtype and message_length", so the
hinge the round trip turns on is getting those bytes back out of a message
the client received.

The obvious `header.dump()` does not work: that no-argument form is defined
only on ClientMessage, and DumpHeader is a ServerMessage, whose inherited
`Struct.dump(val, buffer)` is an unrelated field serialiser. `_body()` below
does what ClientMessage.dump does, minus the type and length prefix, using
the same per-field writers - so it reproduces the body the server sent.
"""

from __future__ import annotations

import io
import os.path

from edb.common import binwrapper
from edb.protocol import messages
from edb.protocol import protocol  # type: ignore
from edb.testbase import server as tb


def _body(msg: messages.ServerMessage) -> bytes:
    """The message's wire body: everything after mtype and message_length.

    `Struct.dump` is a classmethod over the same per-field writers that
    ClientMessage.dump() uses, and skips mtype and message_length itself -
    so this is the server-message counterpart of `dump()[5:]`, without
    duplicating the field walk.
    """
    iobuf = io.BytesIO()
    type(msg).dump(msg, binwrapper.BinWrapper(iobuf))
    return iobuf.getvalue()


class TestDumpRestore(tb.QueryTestCase):
    SCHEMA = os.path.join(
        os.path.dirname(__file__), 'schemas', 'dump_restore.esdl'
    )

    SETUP = os.path.join(
        os.path.dirname(__file__), 'schemas', 'dump_restore_setup.edgeql'
    )

    # DUMP and RESTORE both refuse to run inside a transaction, and the base
    # class wraps each test in one. test_branching.py turns it off for the
    # same reason. What that costs is the transactional rollback between
    # tests; these only ever create their own scratch branch and drop it in
    # a finally, so there is nothing left behind for a retry to trip over.
    TRANSACTION_ISOLATION = False

    async def _dump(
        self, dbname: str
    ) -> tuple[messages.DumpHeader, list[messages.DumpBlock]]:
        """Dump `dbname` over the binary protocol.

        The server answers a Dump with one DumpHeader, then zero or more
        DumpBlocks, then a CommandComplete whose status is 'DUMP'.
        """
        con = await protocol.new_connection(
            **self.get_connect_args(database=dbname),
        )
        try:
            await con.connect()
            # flags is an EnumOf, whose dump() reads val.value - a bare 0
            # raises AttributeError before anything reaches the wire.
            await con.send(
                messages.Dump(annotations=[], flags=messages.DumpFlag(0))
            )
            header = await con.recv_match(messages.DumpHeader)

            blocks: list[messages.DumpBlock] = []
            while True:
                msg = await con.recv()
                if isinstance(msg, messages.DumpBlock):
                    blocks.append(msg)
                elif isinstance(msg, messages.CommandComplete):
                    self.assertEqual(msg.status, 'DUMP')
                    break
                else:
                    raise AssertionError(
                        f'unexpected message during dump: {msg!r}'
                    )
            return header, blocks
        finally:
            await con.aclose()

    async def _restore(
        self,
        dbname: str,
        header: messages.DumpHeader,
        blocks: list[messages.DumpBlock],
    ) -> None:
        """Restore a dump into the (empty) database `dbname`."""
        con = await protocol.new_connection(
            **self.get_connect_args(database=dbname),
        )
        try:
            await con.connect()
            # attributes must be empty: the server rejects anything else
            # with "unexpected attributes". jobs is read and discarded.
            await con.send(
                messages.Restore(
                    attributes=[],
                    jobs=1,
                    header_data=_body(header),
                )
            )
            await con.recv_match(messages.RestoreReady)

            for block in blocks:
                await con.send(messages.RestoreBlock(block_data=_body(block)))
            await con.send(messages.RestoreEof())

            msg = await con.recv()
            if not isinstance(msg, messages.CommandComplete):
                raise AssertionError(f'restore did not complete: {msg!r}')
            self.assertEqual(msg.status, 'RESTORE')
        finally:
            await con.aclose()

    async def _round_trip(self, target: str) -> None:
        """Dump this test's database and restore it into a fresh `target`."""
        header, blocks = await self._dump(self.get_database_name())
        await self.con.execute(f'create empty branch {target}')
        try:
            await self._restore(target, header, blocks)
        except Exception:
            await tb.drop_db(self.con, target)
            raise

    def setUp(self):
        super().setUp()
        if not self.has_create_database:
            self.skipTest('dump/restore needs a second branch to restore into')
        if not self.is_superuser:
            self.skipTest('restore requires superuser')

    def test_dump_restore_messages_serialize(self):
        # Every client message this suite sends, serialised. It touches no
        # connection, so it names a malformed message directly instead of
        # surfacing as a failure in all four round trips at once.
        #
        # Worth its own test because the first version of this file passed
        # `flags=0` to Dump, and EnumOf.dump() reads `val.value` - so it
        # raised AttributeError before a byte reached the wire, and took
        # all four round trips down with it for a reason that had nothing
        # to do with dump or restore.
        for msg in (
            messages.Dump(annotations=[], flags=messages.DumpFlag(0)),
            messages.Restore(attributes=[], jobs=1, header_data=b'hdr'),
            messages.RestoreBlock(block_data=b'blk'),
            messages.RestoreEof(),
        ):
            with self.subTest(message=type(msg).__name__):
                self.assertTrue(msg.dump())

    async def test_dump_restore_schema_01(self):
        # Schema fidelity: the restored database must describe identically.
        # `describe schema as sdl` is the same comparison test_branching.py
        # uses for branch fidelity, and for the same reason - it is the
        # server's own rendering, so it cannot drift from the schema it
        # describes the way a hand-written expectation would.
        target = f'dumprestore_schema_{self.get_database_name()}'
        original = await self.con.query_single('describe schema as sdl')
        await self._round_trip(target)
        try:
            con2 = await self.connect(database=target)
            try:
                restored = await con2.query_single('describe schema as sdl')
            finally:
                await con2.aclose()
            self.assertEqual(original, restored)
        finally:
            await tb.drop_db(self.con, target)

    async def test_dump_restore_data_01(self):
        # Data fidelity across every scalar kind the corpus carries.
        query = '''
            select Widget {
                name, count, weight, ratio, small, medium, active,
                created, local_day, span, ident, blob, payload, color,
                tags, pair, label,
            }
            order by .name
        '''
        target = f'dumprestore_data_{self.get_database_name()}'
        original = await self.con.query_json(query)
        await self._round_trip(target)
        try:
            con2 = await self.connect(database=target)
            try:
                restored = await con2.query_json(query)
            finally:
                await con2.aclose()
            self.assertEqual(original, restored)
        finally:
            await tb.drop_db(self.con, target)

    async def test_dump_restore_links_01(self):
        # Links, multi links and link properties: a dump that carried the
        # objects but lost the edges between them would still pass a
        # scalar-only comparison.
        query = '''
            select Box {
                name,
                primary: { name },
                contains: { name, @quantity } order by .name,
            }
            order by .name
        '''
        target = f'dumprestore_links_{self.get_database_name()}'
        original = await self.con.query_json(query)
        await self._round_trip(target)
        try:
            con2 = await self.connect(database=target)
            try:
                restored = await con2.query_json(query)
            finally:
                await con2.aclose()
            self.assertEqual(original, restored)
        finally:
            await tb.drop_db(self.con, target)

    async def test_dump_restore_empty_01(self):
        # A dump of a database with no user objects still has to restore.
        # This is the case a round trip built only on populated fixtures
        # never reaches, and it is where an off-by-one in block handling
        # would show: there may be no DumpBlock at all.
        source = f'dumprestore_src_{self.get_database_name()}'
        target = f'dumprestore_dst_{self.get_database_name()}'
        await self.con.execute(f'create empty branch {source}')
        try:
            header, blocks = await self._dump(source)
            await self.con.execute(f'create empty branch {target}')
            try:
                await self._restore(target, header, blocks)
                con2 = await self.connect(database=target)
                try:
                    count = await con2.query_single(
                        'select count(schema::ObjectType filter not .builtin)'
                    )
                finally:
                    await con2.aclose()
                self.assertEqual(count, 0)
            finally:
                await tb.drop_db(self.con, target)
        finally:
            await tb.drop_db(self.con, source)
