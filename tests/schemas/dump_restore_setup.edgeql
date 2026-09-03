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

# Values chosen to be awkward rather than tidy: an empty array and an empty
# string, a negative number, a zero, the int16/int32 extremes, a
# non-ASCII string, bytes containing a NUL, and a Widget with every
# optional property left empty. A round trip that only moves comfortable
# values is not evidence of much.

insert Widget {
    name := 'alpha',
    count := 1,
    weight := 1.5,
    ratio := <float32>0.25,
    small := <int16>32767,
    medium := <int32>-2147483648,
    active := true,
    created := <datetime>'2026-01-02T03:04:05.678901Z',
    local_day := <cal::local_date>'2026-01-02',
    span := <duration>'PT1H30M',
    ident := <uuid>'01234567-89ab-cdef-0123-456789abcdef',
    blob := b'\x00\x01\xfe\xff',
    payload := <json>'{"nested": {"a": [1, 2, 3]}, "null": null}',
    color := Color.Red,
    tags := ['one', 'two', ''],
    pair := ('left', 42),
};

insert Widget {
    name := 'béta ☃',
    count := 0,
    weight := -0.0,
    ratio := <float32>-1.5,
    small := <int16>-32768,
    medium := <int32>2147483647,
    active := false,
    created := <datetime>'1970-01-01T00:00:00Z',
    local_day := <cal::local_date>'1970-01-01',
    span := <duration>'PT0S',
    ident := <uuid>'00000000-0000-0000-0000-000000000000',
    blob := b'',
    payload := <json>'[]',
    color := Color.Blue,
    tags := <array<str>>[],
    pair := ('', -1),
};

# Every optional property empty, to prove the dump carries absence as
# absence rather than as a default.
insert Widget {
    name := 'sparse',
    count := -9223372036854775808,
};

insert Box {
    name := 'crate',
    primary := (select Widget filter .name = 'alpha'),
    contains := (
        for w in (select Widget filter .name in {'alpha', 'béta ☃'})
        union (
            select w { @quantity := <int64>len(w.name) }
        )
    ),
};

# A Box with no links at all, so the empty-set case is covered too.
insert Box {
    name := 'empty crate',
};
