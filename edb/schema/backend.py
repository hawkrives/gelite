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

"""The frontend's side of its dependency on the backend, inverted.

`edb/edgeql/`, `edb/ir/` and `edb/schema/` must not import the backend, so
that the backend can be forked and replaced without touching them. Two
schema checks genuinely need to ask the backend a question, so the backend
answers them by registering here on import rather than being called by name.

Both questions are refinements: the frontend has already applied its own
rules by the time it asks, and the backend can only narrow what is allowed,
never widen it. That is why an unregistered backend is not an error - the
frontend's own rules still hold, and the checks below degrade to permitting
what the backend would have had the final say on. A process that can
execute DDL has imported `edb.sqlite`, and therefore registered, long before
any of this runs.
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Optional, TYPE_CHECKING

from edb import errors

if TYPE_CHECKING:
    from edb.common import parsing

    from . import expr as s_expr
    from . import schema as s_schema
    from . import types as s_types


class BackendHooks(NamedTuple):
    lower_expr: Callable[[s_expr.CompiledExpression], None]
    """Lower a compiled expression, raising if it cannot be lowered.

    Called for its exceptions - the lowered form is thrown away. Its only
    purpose is to reject an unlowerable constraint or index expression while
    the DDL that introduced it is still on screen.
    """

    supports_range_type: Callable[[s_schema.Schema, s_types.Type], bool]
    """Whether the backend can store this range or multirange type."""


_hooks: Optional[BackendHooks] = None


def register(hooks: BackendHooks) -> None:
    global _hooks
    _hooks = hooks


def try_lower_expr(
    compiled_expr: s_expr.CompiledExpression,
    span: Optional[parsing.Span],
) -> None:
    """Reject an expression the backend cannot lower, blaming `span`.

    Without this the same failure surfaces later, from a compiler that has
    no idea which piece of DDL is at fault.
    """
    if _hooks is None:
        return

    try:
        _hooks.lower_expr(compiled_expr)
    except errors.EdgeDBError as exception:
        exception.set_span(span)
        raise


def supports_range_type(
    schema: s_schema.Schema,
    stype: s_types.Type,
) -> bool:
    """Whether the backend can store `stype`, a range or multirange type.

    `std::anypoint` is meant to be the set of types a range can be built
    over, but it is wider than any backend actually supports - `range<
    duration>` satisfies every frontend rule and no backend has a range
    type for it.
    """
    if _hooks is None:
        return True

    return _hooks.supports_range_type(schema, stype)
