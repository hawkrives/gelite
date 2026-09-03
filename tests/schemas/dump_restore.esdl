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

# A corpus for the dump/restore round trip (#26). Deliberately spans the
# things a dump has to carry - scalars of every surviving kind, collections,
# an enum, an abstract base, single and multi links, a link property, a
# computed, a constraint and an index - while staying inside what this fork
# still builds.
#
# It must not use ranges, multiranges, decimal, bigint or full-text search:
# all four are deferred (#75, design section 5), so a corpus that assumed
# them would fail for reasons that have nothing to do with dump or restore.

scalar type Color extending enum<Red, Green, Blue>;

abstract type HasName {
    required name: str {
        constraint exclusive;
    }
}

type Widget extending HasName {
    required count: int64;
    weight: float64;
    ratio: float32;
    small: int16;
    medium: int32;
    active: bool;
    created: datetime;
    local_day: cal::local_date;
    span: duration;
    ident: uuid;
    blob: bytes;
    payload: json;
    color: Color;
    tags: array<str>;
    pair: tuple<str, int64>;

    index on (.count);

    label := .name ++ '/' ++ <str>.count;
}

type Box extending HasName {
    single primary: Widget;

    multi contains: Widget {
        quantity: int64;
    }
}
