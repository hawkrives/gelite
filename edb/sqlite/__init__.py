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

# This file must contain no code, only comments.
#
# setup.py's ci_helper computes the build cache keys by calling
# get_cache_src_dirs(), which does find_spec('edb.sqlite.metaschema') -
# and find_spec on a submodule imports its parent package. That runs at
# the very start of the build job, before the Rust extension exists, so
# anything imported from here that reaches edb.schema (and through it
# edb.common.span, which imports edb._edgeql_parser) fails the build
# with ModuleNotFoundError.
#
# The schema.backend hooks are registered from edb/sqlite/common.py
# instead, which every other backend module imports and which nothing in
# the build path touches. tests/test_sourcecode.py enforces the emptiness.
