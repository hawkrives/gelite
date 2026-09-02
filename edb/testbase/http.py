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


from __future__ import annotations
from typing import (
    Any,
    Callable,
    Optional,
)

import http.server
import json
import threading
import urllib.parse
import urllib.request
import dataclasses


from edb.common import assert_data_shape

from . import server


bag = assert_data_shape.bag


class BaseHttpTest(server.QueryTestCase):
    @classmethod
    async def _wait_for_db_config(
        cls,
        config_key,
        *,
        server=None,
        instance_config=False,
        value=None,
        is_reset=False,
    ):
        dbname = cls.get_database_name()
        # Wait for the database config changes to propagate to the
        # server by watching a debug endpoint
        async for tr in cls.try_until_succeeds(
            ignore=AssertionError,
            timeout=120,
        ):
            async with tr:
                with cls.http_con(server) as http_con:
                    (
                        rdata,
                        _headers,
                        _status,
                    ) = cls.http_con_request(
                        http_con,
                        prefix="",
                        path="server-info",
                    )
                    data = json.loads(rdata)
                    if "databases" not in data:
                        # multi-tenant instance - use the first tenant
                        data = next(iter(data["tenants"].values()))
                    if instance_config:
                        config = data["instance_config"]
                    else:
                        config = data["databases"][dbname]["config"]
                    if is_reset:
                        if config_key in config:
                            raise AssertionError("database config not ready")
                    else:
                        if config_key not in config:
                            raise AssertionError("database config not ready")
                        if value and config[config_key] != value:
                            raise AssertionError("database config not ready")


class BaseHttpExtensionTest(BaseHttpTest):
    @classmethod
    def get_extension_path(cls):
        raise NotImplementedError

    @classmethod
    def get_api_prefix(cls):
        extpath = cls.get_extension_path()
        dbname = cls.get_database_name()
        return f"/branch/{dbname}/{extpath}"


class MockHttpServerHandler(http.server.BaseHTTPRequestHandler):
    def get_server_and_path(self) -> tuple[str, str]:
        server = f'http://{self.headers.get("Host")}'
        return server, self.path

    def do_GET(self):
        self.close_connection = False
        server, path = self.get_server_and_path()
        self.server.owner.handle_request("GET", server, path, self)

    def do_POST(self):
        self.close_connection = False
        server, path = self.get_server_and_path()
        self.server.owner.handle_request("POST", server, path, self)

    def log_message(self, *args):
        pass


class MultiHostMockHttpServerHandler(MockHttpServerHandler):
    def get_server_and_path(self) -> tuple[str, str]:
        # Path looks like:
        # http://127.0.0.1:32881/https%3A//slack.com/.well-known/openid-configuration
        raw_url = urllib.parse.unquote(self.path.lstrip("/"))
        url = urllib.parse.urlparse(raw_url)
        return (f"{url.scheme}://{url.netloc}", url.path.lstrip("/"))


ResponseType = tuple[str, int] | tuple[str, int, dict[str, str]]


@dataclasses.dataclass
class RequestDetails:
    headers: dict[str, str | Any]
    query_params: dict[str, list[str]]
    body: Optional[str]


class MockHttpServer:
    def __init__(
        self,
        handler_type: type[MockHttpServerHandler] = MockHttpServerHandler,
        port: int = 0,
    ) -> None:
        self._port = port
        self.has_started = threading.Event()
        self.routes: dict[
            tuple[str, str, str],
            (
                ResponseType
                | Callable[
                    [MockHttpServerHandler, RequestDetails], ResponseType
                ]
            ),
        ] = {}
        self.requests: dict[tuple[str, str, str], list[RequestDetails]] = {}
        self.url: Optional[str] = None
        self.handler_type = handler_type

    def get_base_url(self) -> str:
        if self.url is None:
            raise RuntimeError("mock server is not running")
        return self.url

    def register_route_handler(
        self,
        method: str,
        server: str,
        path: str,
    ):
        def wrapper(
            handler: (
                ResponseType
                | Callable[
                    [MockHttpServerHandler, RequestDetails], ResponseType
                ]
            ),
        ):
            self.routes[(method, server, path)] = handler
            return handler

        return wrapper

    def handle_request(
        self,
        method: str,
        server: str,
        path: str,
        handler: MockHttpServerHandler,
    ):
        # `handler` is documented here:
        # https://docs.python.org/3/library/http.server.html#http.server.BaseHTTPRequestHandler
        key = (method, server, path)
        if key not in self.requests:
            self.requests[key] = []

        # Parse and save the request details
        parsed_path = urllib.parse.urlparse(path)
        headers = {k.lower(): v for k, v in dict(handler.headers).items()}
        query_params = urllib.parse.parse_qs(parsed_path.query)
        if "content-length" in headers:
            body = handler.rfile.read(int(headers["content-length"])).decode()
        else:
            body = None

        request_details = RequestDetails(
            headers=headers,
            query_params=query_params,
            body=body,
        )
        self.requests[key].append(request_details)
        if key not in self.routes:
            error_message = (
                f"No route handler for {key}\n\n"
                f"Available routes:\n{self.routes}"
            )
            handler.send_error(404, message=error_message)
            return

        registered_handler = self.routes[key]

        if callable(registered_handler):
            try:
                handler_result = registered_handler(handler, request_details)
                if len(handler_result) == 2:
                    response, status = handler_result
                    additional_headers = None
                elif len(handler_result) == 3:
                    response, status, additional_headers = handler_result
            except Exception:
                handler.send_error(500)
                raise
        else:
            if len(registered_handler) == 2:
                response, status = registered_handler
                additional_headers = None
            elif len(registered_handler) == 3:
                response, status, additional_headers = registered_handler

        accept_header = request_details.headers.get(
            "accept", "application/json"
        )

        if (
            accept_header.startswith("application/json")
            or (
                accept_header.startswith("application/")
                and "vnd." in accept_header
                and "+json" in accept_header
            )
            or accept_header == "*/*"
        ):
            content_type = "application/json"
        elif accept_header.startswith("application/x-www-form-urlencoded"):
            content_type = "application/x-www-form-urlencoded"
        else:
            handler.send_error(
                415, f"Unsupported accept header: {accept_header}"
            )
            return

        data = response.encode()

        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        if additional_headers is not None:
            for header, value in additional_headers.items():
                handler.send_header(header, value)
        handler.end_headers()
        handler.wfile.write(data)

    def start(self):
        assert not hasattr(self, "_http_runner")
        self._http_runner = threading.Thread(target=self._http_worker)
        self._http_runner.start()
        self.has_started.wait()
        self.url = f"http://{self._address[0]}:{self._address[1]}/"

    def __enter__(self):
        self.start()
        return self

    def _http_worker(self):
        self._http_server = http.server.HTTPServer(
            ("localhost", self._port), self.handler_type
        )
        self._http_server.owner = self
        self._address = self._http_server.server_address
        self.has_started.set()
        self._http_server.serve_forever(poll_interval=0.01)
        self._http_server.server_close()

    def stop(self):
        self._http_server.shutdown()
        if self._http_runner is not None:
            self._http_runner.join(timeout=60)
            if self._http_runner.is_alive():
                raise RuntimeError("Mock HTTP server failed to stop")
            self._http_runner = None

    def __exit__(self, *exc):
        self.stop()
        self.url = None
