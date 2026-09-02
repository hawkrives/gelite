#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2024-present MagicStack Inc. and the EdgeDB authors.
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

# Upstream indexes Post with an fts::index built over
# ext::pg_unaccent::unaccent(.body). Full-text search is deferred (#75), and
# the index is what test_edgeql_ext_pg_unaccent_02 needs - but pg_unaccent
# itself is a kept extension (#24), and _01 tests unaccent() directly with no
# FTS involved. Keeping the index here would fail this schema at creation and
# take _01 down with it, losing the only coverage the kept extension has.
type Post {
    title: str;
    body: str;
};
