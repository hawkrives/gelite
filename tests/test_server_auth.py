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

import asyncio
import os
import pathlib
import platform
import signal
import tempfile
import unittest

import edgedb

from edb import errors
from edb import protocol
from edb.server import args
from edb.testbase import cluster as edbcluster
from edb.server.auth import JWKSet, generate_gel_token, load_secret_key
from edb.schema import defines as s_def
from edb.testbase import server as tb


class TestServerAuth(tb.ConnectedTestCase):
    PARALLELISM_GRANULARITY = 'system'
    TRANSACTION_ISOLATION = False

    async def test_server_auth_01(self):
        if not self.has_create_role:
            self.skipTest('create role is not supported by the backend')

        await self.con.query(f'''
            CREATE SUPERUSER ROLE foo {{
                SET password := 'foo-pass';
                SET branches := 'main';
            }}
        ''')

        # bad password
        with self.assertRaisesRegex(
            edgedb.AuthenticationError, 'authentication failed'
        ):
            await self.connect(
                user='foo',
                password='wrong',
            )

        # good password
        conn = await self.connect(
            user='foo',
            password='foo-pass',
        )
        await conn.aclose()
        # good password, non-allowed database
        with self.assertRaisesRegex(
            edgedb.AuthenticationError,
            "authentication failed: user does not have permission for "
            "database branch 'auth_failure'",
        ):
            await self.connect(
                user='foo',
                password='foo-pass',
                database='auth_failure',
            )

        # __edgedbsys__ on a role with a whitelist -- should still work
        syscon = await self.connect(
            user='foo',
            password='foo-pass',
            database='__edgedbsys__',
        )
        await syscon.aclose()

        # Force foo to use a JWT so auth fails
        await self.con.query('''
            CONFIGURE INSTANCE INSERT Auth {
                comment := 'foo-jwt',
                priority := -1,
                user := 'foo',
                method := (INSERT JWT {
                    transports := "SIMPLE_HTTP",
                }),
            }
        ''')

        await self.assert_query_result(
            r"""
                SELECT cfg::Auth {
                    method: { transports },
                }
                FILTER any(.user = 'foo')
            """,
            [{'method': {'transports': ['SIMPLE_HTTP']}}],
        )

        await self.con.query('''
            CONFIGURE INSTANCE RESET Auth
            filter .comment = 'foo-jwt'
        ''')

        await self.con.query('''
            CONFIGURE INSTANCE INSERT Auth {
                comment := 'test',
                priority := 0,
                method := (INSERT Trust),
            }
        ''')

        try:
            # bad password, but the trust method doesn't care
            conn = await self.connect(
                user='foo',
                password='wrong',
            )
            await conn.aclose()

            # insert password auth with a higher priority
            await self.con.query('''
                CONFIGURE INSTANCE INSERT Auth {
                    comment := 'test-2',
                    priority := -1,
                    method := (INSERT SCRAM),
                }
            ''')

            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'authentication failed',
            ):
                # bad password is bad again
                await self.connect(
                    user='foo',
                    password='wrong',
                )

        finally:
            await self.con.query('''
                CONFIGURE INSTANCE RESET Auth FILTER .comment = 'test'
            ''')

            await self.con.query('''
                CONFIGURE INSTANCE RESET Auth FILTER .comment = 'test-2'
            ''')

            await self.con.query('''
                DROP ROLE foo;
            ''')

        # Basically the second test, but we can't run it concurrently
        # because disabling Auth above conflicts with the following test

        await self.con.query('''
            CREATE SUPERUSER ROLE bar {
                SET password_hash := 'SCRAM-SHA-256$4096:SHzNmIppMwXnPSWgY2yMvg==$5zmnXMm9+mn2nseKPF1NTKvuoBPVSWgxHrnptxpQgcU=:/c1vJV+MmS7v9vv6CDVo56OyOJkNd3F+m3JIBB1U7ho=';
            }
        ''')  # noqa

        try:
            conn = await self.connect(
                user='bar',
                password='bar-pass',
            )
            await conn.aclose()

            await self.con.query('''
                ALTER ROLE bar {
                    SET password_hash := 'SCRAM-SHA-256$4096:mWDBY53yzQ4aDet5erBmbg==$ZboQEMuUhC6+1SChp2bx1qSRBZGAnyV4I8T/iK+qeEs=:B7yF2k10tTH2RHayOg3rw4Q6wqf+Fj5CuXR/9CyZ8n8=';
                }
            ''')  # noqa

            conn = await self.connect(
                user='bar',
                password='bar-pass-2',
            )
            await conn.aclose()

            # bad (old) password
            with self.assertRaisesRegex(
                edgedb.AuthenticationError, 'authentication failed'
            ):
                await self.connect(
                    user='bar',
                    password='bar-pass',
                )

            with self.assertRaisesRegex(
                edgedb.EdgeQLSyntaxError,
                'cannot specify both `password` and `password_hash`'
                ' in the same statement',
            ):
                await self.con.query('''
                    CREATE SUPERUSER ROLE bar1 {
                        SET password := 'hello';
                        SET password_hash := 'SCRAM-SHA-256$4096:SHzNmIppMwXnPSWgY2yMvg==$5zmnXMm9+mn2nseKPF1NTKvuoBPVSWgxHrnptxpQgcU=:/c1vJV+MmS7v9vv6CDVo56OyOJkNd3F+m3JIBB1U7ho=';
                    }
                ''')  # noqa

            with self.assertRaisesRegex(
                edgedb.InvalidValueError, 'invalid SCRAM verifier'
            ):
                await self.con.query('''
                    CREATE SUPERUSER ROLE bar2 {
                        SET password_hash := 'SCRAM-BLAKE2B$4096:SHzNmIppMwXnPSWgY2yMvg==$5zmnXMm9+mn2nseKPF1NTKvuoBPVSWgxHrnptxpQgcU=:/c1vJV+MmS7v9vv6CDVo56OyOJkNd3F+m3JIBB1U7ho=';
                    }
                ''')  # noqa

        finally:
            await self.con.query("DROP ROLE bar")

    async def test_server_auth_02(self):
        if not self.has_create_role:
            self.skipTest('create role is not supported by the backend')

        try:
            await self.con.query('''
                CREATE SUPERUSER ROLE foo {
                    SET password := 'foo-pass';
                }
            ''')

            await self.con.query('''
                CREATE SUPERUSER ROLE bar {
                    SET password := 'bar-pass';
                }
            ''')

            await self.con.query('''
                CONFIGURE INSTANCE INSERT Auth {
                    comment := 'test-02',
                    priority := 0,
                    method := (INSERT SCRAM),
                    user := 'foo',
                }
            ''')

            # good password with configured Auth
            conn = await self.connect(
                user='foo',
                password='foo-pass',
            )
            await conn.aclose()

            # good password but Auth is not configured
            # (should default to SCRAM and succeed)
            conn2 = await self.connect(user='bar', password='bar-pass')
            await conn2.aclose()
        finally:
            await self.con.query('''
                CONFIGURE INSTANCE RESET Auth FILTER .comment = 'test-02'
            ''')

            await self.con.query('''
                DROP ROLE foo;
            ''')

            await self.con.query('''
                DROP ROLE bar;
            ''')

    async def test_server_auth_permissions_consistency_01(self):
        # Check that changing password doesn't impact permissions

        await self.con.query('''
            CREATE ROLE foo {
                SET password := 'secret';
                SET permissions := custom::bar
            }
        ''')  # noqa

        try:
            conn = await self.connect(
                user='foo',
                password='secret',
            )
            await conn.aclose()

            await self.con.query('''
                ALTER ROLE foo {
                    SET password := 'super secret';
                }
            ''')  # noqa

            await self.assert_query_result(
                r"""
                    SELECT sys::Role {
                        name,
                        permissions,
                    }
                    FILTER .name = 'foo'
                    ORDER BY .name
                """,
                [
                    {
                        'name': 'foo',
                        'permissions': ['custom::bar'],
                    },
                ],
            )

            conn = await self.connect(
                user='foo',
                password='super secret',
            )
            await conn.aclose()

        finally:
            await self.con.query("DROP ROLE foo")

    async def test_server_auth_permissions_consistency_02(self):
        # Check that changing permissions doesn't impact password

        await self.con.query('''
            CREATE ROLE foo {
                SET password := 'secret';
                SET permissions := custom::bar
            }
        ''')  # noqa

        try:
            conn = await self.connect(
                user='foo',
                password='secret',
            )
            await conn.aclose()

            await self.con.query('''
                ALTER ROLE foo {
                    SET permissions := custom::baz;
                }
            ''')  # noqa

            await self.assert_query_result(
                r"""
                    SELECT sys::Role {
                        name,
                        permissions,
                    }
                    FILTER .name = 'foo'
                    ORDER BY .name
                """,
                [
                    {
                        'name': 'foo',
                        'permissions': ['custom::baz'],
                    },
                ],
            )

            conn = await self.connect(
                user='foo',
                password='secret',
            )
            await conn.aclose()

        finally:
            await self.con.query("DROP ROLE foo")

    async def test_long_role_name(self):
        with self.assertRaisesRegex(
            edgedb.SchemaDefinitionError,
            r'Role names longer than \d+ '
            r'characters are not supported',
        ):
            await self.con.execute(
                f'CREATE SUPERUSER ROLE myrole_{"x" * s_def.MAX_NAME_LENGTH};'
            )

    async def test_server_auth_jwt_1(self):
        jwk_fd, jwk_file = tempfile.mkstemp()

        jws = JWKSet()
        jws.generate(kid=None, kty="ES256")
        with open(jwk_fd, "wb") as f:
            f.write(jws.export_pem())

        async with tb.start_edgedb_server(
            jws_key_file=pathlib.Path(jwk_file),
            default_auth_method=args.ServerAuthMethod.JWT,
        ) as sd:
            base_sk = generate_gel_token(jws)
            conn = await sd.connect(secret_key=base_sk)
            await conn.execute('''
                CREATE SUPERUSER ROLE foo {
                    SET password := 'foo-pass';
                }
            ''')
            # Force foo to use passwords for simple auth so auth fails
            await conn.query('''
                CONFIGURE INSTANCE INSERT Auth {
                    comment := 'foo-jwt',
                    priority := -1,
                    user := 'foo',
                    method := (INSERT Password {
                        transports := "SIMPLE_HTTP",
                    }),
                }
            ''')
            await conn.aclose()

            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'authentication failed: no authorization data provided',
            ):
                await sd.connect()

            # bad secret keys
            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'authentication failed: malformed JWT',
            ):
                await sd.connect(secret_key='wrong')

            sk = generate_gel_token(jws)
            corrupt_sk = sk[:50] + "0" + sk[51:]

            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'authentication failed: Verification failed',
            ):
                await sd.connect(secret_key=corrupt_sk)

            # Try to mess up the *signature* part of it
            wrong_sk = sk[:-20] + ("1" if sk[-20] == "0" else "0") + sk[-20:]
            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'authentication failed: Verification failed',
            ):
                await sd.connect(secret_key=wrong_sk)

            # Good key (control check, mostly)
            # Good key but nonexistant user
            # Good key but user needs password auth

            good_keys = [
                [],
                [("roles", ["admin"])],
                [("databases", ["main"])],
                [("instances", ["localtest"])],
            ]

            for params in good_keys:
                params_dict = dict(params)
                with self.subTest(**params_dict):
                    sk = generate_gel_token(jws, **params_dict)
                    conn = await sd.connect(secret_key=sk)
                    await conn.aclose()

            bad_keys = {
                (
                    ("roles", ("bad-role",)),
                ): 'secret key does not authorize access ' + 'in role "admin"',
                (
                    ("databases", ("bad-database",)),
                ): 'secret key does not authorize access '
                + 'to database "main"',
                (
                    ("instances", ("bad-instance",)),
                ): 'secret key does not authorize access ' + 'to this instance',
            }

            for params, msg in bad_keys.items():
                params_dict = dict(params)
                with self.subTest(**params_dict):
                    sk = generate_gel_token(jws, **params_dict)
                    with self.assertRaisesRegex(
                        edgedb.AuthenticationError,
                        "authentication failed: " + msg,
                    ):
                        await sd.connect(secret_key=sk)

    async def test_server_auth_jwt_2(self):
        jwk_fd, jwk_file = tempfile.mkstemp()

        jws = JWKSet()
        jws.generate(kid=None, kty="ES256")
        with open(jwk_fd, "wb") as f:
            f.write(jws.export_pem())

        allowlist_fd, allowlist_file = tempfile.mkstemp()
        os.close(allowlist_fd)

        revokelist_fd, revokelist_file = tempfile.mkstemp()
        os.close(revokelist_fd)

        subject = "test"
        key_id = "foobar"

        with self.assertRaisesRegex(edbcluster.ClusterError, "cannot load JWT"):
            async with tb.start_edgedb_server(
                jws_key_file=jwk_file,
                jwt_sub_allowlist_file='/tmp/non_existant',
                jwt_revocation_list_file='/tmp/non_existant',
            ):
                pass

        async with tb.start_edgedb_server(
            jws_key_file=jwk_file,
            jwt_sub_allowlist_file=allowlist_file,
            jwt_revocation_list_file=revokelist_file,
        ) as sd:
            jwk = load_secret_key(pathlib.Path(jwk_file))

            # enable JWT
            conn = await sd.connect()
            await conn.query("""
                CONFIGURE INSTANCE INSERT Auth {
                    comment := 'test',
                    priority := 0,
                    method := (INSERT JWT {
                        transports := cfg::ConnectionTransport.TCP
                    }),
                }
            """)
            await conn.aclose()

            # Try connecting with "test" not being in the allowlist.
            sk = generate_gel_token(
                jwk,
                sub=subject,
                jti=key_id,
            )
            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'authentication failed: Verification failed',
            ):
                await sd.connect(secret_key=sk)

            # Now add it to the allowlist.
            with open(allowlist_file, "w") as f:
                f.write(subject)
            os.kill(sd.pid, signal.SIGHUP)

            await asyncio.sleep(1)

            conn = await sd.connect(secret_key=sk)
            await conn.aclose()

            # Now revoke the key
            with open(revokelist_file, "w") as f:
                f.write(key_id)
            os.kill(sd.pid, signal.SIGHUP)

            await asyncio.sleep(1)

            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'authentication failed: Verification failed',
            ):
                await sd.connect(secret_key=sk)

    async def test_server_auth_multiple_methods(self):
        jwk_fd, jwk_file = tempfile.mkstemp()

        jws = JWKSet()
        jws.generate(kid=None, kty="ES256")
        with open(jwk_fd, "wb") as f:
            f.write(jws.export_pem())
        jwk = load_secret_key(pathlib.Path(jwk_file))
        async with tb.start_edgedb_server(
            jws_key_file=pathlib.Path(jwk_file),
            default_auth_method=args.ServerAuthMethods(
                {
                    args.ServerConnTransport.TCP: [
                        args.ServerAuthMethod.JWT,
                        args.ServerAuthMethod.Scram,
                    ],
                    args.ServerConnTransport.SIMPLE_HTTP: [
                        args.ServerAuthMethod.Password,
                        args.ServerAuthMethod.JWT,
                    ],
                }
            ),
        ) as sd:
            base_sk = generate_gel_token(jwk)
            conn = await sd.connect(secret_key=base_sk)
            await conn.aclose()

            # bad secret keys
            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'authentication failed: malformed JWT',
            ):
                await sd.connect(secret_key='wrong', password=None)

            # But connecting with the default password should still work
            # because we are defaulting to Scram/JWT
            c1 = await sd.connect(secret_key='wrong')
            await c1.aclose()

    async def test_server_auth_in_transaction(self):
        if not self.has_create_role:
            self.skipTest('create role is not supported by the backend')

        async with self.con.transaction():
            await self.con.query('''
                CREATE SUPERUSER ROLE foo {
                    SET password := 'foo-pass';
                };
            ''')

        try:
            conn = await self.connect(
                user='foo',
                password='foo-pass',
            )
            await conn.aclose()
        finally:
            await self.con.query('''
                DROP ROLE foo;
            ''')

    @unittest.skipIf(
        platform.system() == "Darwin" and platform.machine() == 'x86_64',
        "Postgres is not getting getting enough shared memory on macos-14 "
        "GitHub runner by default",
    )
    async def test_server_auth_mtls(self):
        if not self.has_create_role:
            self.skipTest('create role is not supported by the backend')

        certs = pathlib.Path(__file__).parent / 'certs'
        client_ca_cert_file = certs / 'client_ca.cert.pem'
        client_ssl_cert_file = certs / 'client.cert.pem'
        client_ssl_key_file = certs / 'client.key.pem'
        async with tb.start_edgedb_server(
            tls_client_ca_file=client_ca_cert_file,
            security=args.ServerSecurityMode.Strict,
        ) as sd:
            # Setup mTLS and extensions
            conn = await sd.connect()
            try:
                await conn.query("CREATE SUPERUSER ROLE ssl_user;")
                await self._test_mtls(
                    sd, client_ssl_cert_file, client_ssl_key_file, False
                )
                await conn.query("""
                    CONFIGURE INSTANCE INSERT Auth {
                        comment := 'test',
                        priority := 0,
                        method := (INSERT mTLS {
                            transports := {
                                cfg::ConnectionTransport.TCP,
                                cfg::ConnectionTransport.HTTP,
                                cfg::ConnectionTransport.SIMPLE_HTTP,
                            },
                        }),
                    }
                """)
                await self._test_mtls(
                    sd, client_ssl_cert_file, client_ssl_key_file, True
                )
            finally:
                await conn.aclose()

    async def _test_mtls(
        self, sd, client_ssl_cert_file, client_ssl_key_file, granted
    ):
        # Verifies mTLS authentication on the binary protocol
        if granted:
            with self.assertRaisesRegex(
                edgedb.AuthenticationError,
                'client certificate required',
            ):
                await sd.connect()
        # FIXME: add mTLS support in edgedb-python

        # Verifies mTLS authentication on binary protocol over HTTP
        if granted:
            with self.http_con(
                sd,
                keep_alive=False,
            ) as con:
                msgs, _, status = self.http_con_binary_request(
                    con, "select 42", user="ssl_user"
                )
            self.assertEqual(status, 200)
            self.assertIsInstance(msgs[0], protocol.ErrorResponse)
            self.assertEqual(
                msgs[0].error_code, errors.AuthenticationError.get_code()
            )
        with self.http_con(
            sd,
            keep_alive=False,
            client_cert_file=client_ssl_cert_file,
            client_key_file=client_ssl_key_file,
        ) as con:
            msgs, _, status = self.http_con_binary_request(
                con, "select 42", user="ssl_user"
            )
        if granted:
            self.assertEqual(status, 200)
            self.assertIsInstance(msgs[0], protocol.CommandDataDescription)
            self.assertIsInstance(msgs[1], protocol.Data)
            self.assertEqual(bytes(msgs[1].data[0].data), b"42")
            self.assertIsInstance(msgs[2], protocol.CommandComplete)
            self.assertEqual(msgs[2].status, "SELECT")
            self.assertIsInstance(msgs[3], protocol.ReadyForCommand)
        else:
            self.assertEqual(status, 200)
            self.assertIsInstance(msgs[0], protocol.ErrorResponse)
            self.assertEqual(
                msgs[0].error_code, errors.AuthenticationError.get_code()
            )
