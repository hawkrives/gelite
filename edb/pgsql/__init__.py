#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2008-present MagicStack Inc. and the EdgeDB authors.
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

from typing import TYPE_CHECKING

from edb.schema import backend as s_backend

if TYPE_CHECKING:
    from edb.schema import expr as s_expr
    from edb.schema import schema as s_schema
    from edb.schema import types as s_types


# Registered here, in the package __init__, so that importing any part of
# the backend registers the whole of it. Both bodies import lazily: this
# module is reached by `from edb.pgsql import common` and must not drag the
# compiler in behind it.


def _lower_expr(compiled_expr: s_expr.CompiledExpression) -> None:
    from edb.pgsql import compiler as pg_compiler

    pg_compiler.compile_ir_to_sql_tree(
        compiled_expr.irast,
        output_format=pg_compiler.OutputFormat.NATIVE,
        singleton_mode=True,
    )


def _supports_range_type(schema: s_schema.Schema, stype: s_types.Type) -> bool:
    from edb.pgsql import types as pgtypes

    try:
        pgtypes.pg_type_from_object(schema, stype)
    except Exception:
        return False
    return True


s_backend.register(
    s_backend.BackendHooks(
        lower_expr=_lower_expr,
        supports_range_type=_supports_range_type,
    )
)
