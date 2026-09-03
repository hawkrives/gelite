#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2020-present MagicStack Inc. and the EdgeDB authors.
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

from typing import Any, TypeVar

from . import messages

_ServerMessage_co = TypeVar('_ServerMessage_co', bound=messages.ServerMessage)

async def new_connection(
    dsn: str | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    secret_key: str | None = None,
    branch: str | None = None,
    database: str | None = None,
    timeout: float = ...,
    tls_ca: str | None = None,
    tls_ca_file: str | None = None,
    tls_security: str = ...,
    credentials: str | None = None,
    credentials_file: str | None = None,
    **kwargs: Any,
) -> Connection: ...

class Connection:
    async def connect(self) -> None: ...
    async def execute(
        self, query: str, state_id: bytes, state: bytes
    ) -> None: ...
    async def sync(self) -> bytes: ...
    async def recv(self) -> messages.ServerMessage: ...
    # Returns an instance of the class it matched, not just the base:
    # `recv_match(DumpHeader)` gives a DumpHeader. _ignore_msg defaults to
    # None in protocol.pyx.
    async def recv_match(
        self,
        msgcls: type[_ServerMessage_co],
        _ignore_msg: type[messages.ServerMessage] | None = None,
        **fields: Any,
    ) -> _ServerMessage_co: ...
    async def send(self, *msgs: messages.ClientMessage) -> None: ...
    async def aclose(self) -> None: ...
