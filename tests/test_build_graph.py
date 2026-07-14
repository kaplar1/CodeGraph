"""
Regression + coverage suite for build_graph.py.

Run with: pytest tests/ -v

Every test here builds a tiny synthetic file tree (no network, no real repo
needed) and asserts on the resulting graph. Several of these encode bugs
that were found by hand against real repos (Flask, Redis, nlohmann/json)
during development — keep them passing before changing the extraction
regexes or resolution logic, since it's very easy to fix one language and
silently break another.
"""
import json
import re
import sys
from pathlib import Path

import build_graph as bg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import render_dashboard as rd  # noqa: E402


def write(root: Path, relpath: str, content: str):
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def edge_exists(graph, source, target, etype="imports"):
    return any(e["source"] == source and e["target"] == target and e["type"] == etype
               for e in graph["edges"])


def bg_edge_exists_defines(graph, source, target):
    return edge_exists(graph, source, target, etype="defines")


def bg_edge_exists_calls(graph, source, target):
    return edge_exists(graph, source, target, etype="calls")


def node(graph, node_id):
    return next((n for n in graph["nodes"] if n["id"] == node_id), None)


# ---------- Python ----------

def test_python_relative_import_resolves(tmp_path):
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/a.py", "from .b import thing\n")
    write(tmp_path, "pkg/b.py", "def thing():\n    pass\n")
    graph = bg.build_graph(str(tmp_path))
    assert edge_exists(graph, "pkg/a.py", "pkg/b.py")


def test_python_src_layout_absolute_import_resolves(tmp_path):
    # Mirrors the real Flask bug: absolute "from flask.helpers import x"
    # must resolve even though the package lives under src/.
    write(tmp_path, "src/flask/__init__.py", "")
    write(tmp_path, "src/flask/helpers.py", "def get_debug_flag():\n    pass\n")
    write(tmp_path, "src/flask/app.py", "from flask.helpers import get_debug_flag\n")
    graph = bg.build_graph(str(tmp_path))
    assert edge_exists(graph, "src/flask/app.py", "src/flask/helpers.py")


def test_python_bare_stdlib_import_does_not_crash_or_create_edge(tmp_path):
    write(tmp_path, "a.py", "import os\nimport sys\n")
    graph = bg.build_graph(str(tmp_path))
    assert graph["stats"]["edge_count"] == 0


# ---------- JS / TS ----------

def test_js_relative_import_resolves(tmp_path):
    write(tmp_path, "src/a.js", "import { helper } from './b';\n")
    write(tmp_path, "src/b.js", "export function helper() {}\n")
    graph = bg.build_graph(str(tmp_path))
    assert edge_exists(graph, "src/a.js", "src/b.js")


def test_js_bare_package_import_is_treated_as_external(tmp_path):
    write(tmp_path, "src/a.js", "import React from 'react';\n")
    graph = bg.build_graph(str(tmp_path))
    assert graph["stats"]["edge_count"] == 0  # no crash, no fabricated edge


# ---------- C ----------

def test_c_quoted_include_resolves_relative_to_file(tmp_path):
    write(tmp_path, "src/main.c", '#include "util.h"\nint main() { return 0; }\n')
    write(tmp_path, "src/util.h", "void helper(void);\n")
    graph = bg.build_graph(str(tmp_path))
    assert edge_exists(graph, "src/main.c", "src/util.h")


def test_c_angle_include_resolves_via_header_index(tmp_path):
    # Mirrors nlohmann/json-style layout: angle-bracket include of a header
    # that lives deeper in the tree, not next to the including file.
    write(tmp_path, "src/main.c", "#include <mylib/util.h>\n")
    write(tmp_path, "include/mylib/util.h", "void helper(void);\n")
    graph = bg.build_graph(str(tmp_path))
    assert edge_exists(graph, "src/main.c", "include/mylib/util.h")


def test_c_comment_does_not_create_false_function_and_does_not_swallow_real_one(tmp_path):
    # Regression test for the real bug found against Redis's util.c. The
    # multi-line, "*"-prefixed comment style matters here: a leading "/*"
    # on its own can't start a false match (the char class excludes "/"),
    # but a *continuation* line like " * Based on the following article
    # (that ..." has a valid anchor right at " * ", and "article (" reads
    # as a plausible function start whose greedy arg-match then swallows
    # everything up to the real function's own closing paren and brace.
    write(tmp_path, "util.c", (
        "/* Convert a value into a string.\n"
        " *\n"
        " * Based on the following article (that apparently does not provide a\n"
        " * novel approach but only publicizes an already used technique):\n"
        " *\n"
        " * https://example.com/some-notes-on-integer-formatting */\n"
        "int ull2string(char *dst, unsigned long long value) {\n"
        "    return 0;\n"
        "}\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    names = [d["name"] for d in node(graph, "util.c")["defines"]]
    assert "ull2string" in names
    assert "article" not in names


def test_c_include_survives_comment_stripping(tmp_path):
    # Regression test: an earlier fix that blanked string-literal contents
    # before def-extraction must NOT touch the quoted path in #include,
    # or every #include silently stops producing edges.
    write(tmp_path, "main.c", '// leading comment\n#include "util.h"\n')
    write(tmp_path, "util.h", "")
    graph = bg.build_graph(str(tmp_path))
    assert edge_exists(graph, "main.c", "util.h")


def test_c_multiline_function_signature_detected(tmp_path):
    write(tmp_path, "math.c", (
        "int\n"
        "add(int a,\n"
        "    int b)\n"
        "{\n"
        "    return a + b;\n"
        "}\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    names = [d["name"] for d in node(graph, "math.c")["defines"]]
    assert "add" in names


def test_c_control_flow_not_misread_as_function(tmp_path):
    write(tmp_path, "loop.c", (
        "int run(int n) {\n"
        "    if (n > 0) {\n"
        "        for (int i = 0; i < n; i++) {\n"
        "            while (i < n) { break; }\n"
        "        }\n"
        "    }\n"
        "    return n;\n"
        "}\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    names = [d["name"] for d in node(graph, "loop.c")["defines"]]
    assert names == ["run"]


# ---------- C++ ----------

def test_cpp_class_definition_detected(tmp_path):
    write(tmp_path, "json.hpp", "class basic_json\n{\npublic:\n    basic_json() {}\n};\n")
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    types = {(d["name"], d["kind"]) for d in node(graph, "json.hpp")["defines"]}
    assert ("basic_json", "class") in types


def test_cpp_template_type_parameter_not_misread_as_class(tmp_path):
    # Regression test for the real bug found against nlohmann/json: template
    # parameters using the "class" keyword (e.g. "class ArrayType = ...")
    # were being captured as fake class definitions.
    write(tmp_path, "json_fwd.hpp", (
        "template<class ObjectType = std::map<std::string, int>,\n"
        "         class ArrayType = std::vector<int>>\n"
        "class basic_json;\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    names = [d["name"] for d in node(graph, "json_fwd.hpp")["defines"]]
    assert "ObjectType" not in names
    assert "ArrayType" not in names


def test_cpp_class_name_not_truncated(tmp_path):
    # Regression test: the negative-lookahead fix for the above bug initially
    # let the regex backtrack mid-identifier, truncating "Allocator" to
    # "Allocato". A word-boundary anchor fixes it.
    write(tmp_path, "alloc.hpp", "class Allocator\n{\n};\n")
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    names = [d["name"] for d in node(graph, "alloc.hpp")["defines"]]
    assert "Allocator" in names
    assert "Allocato" not in names


def test_cpp_namespaced_method_definition_captures_method_name(tmp_path):
    write(tmp_path, "widget.cpp", (
        '#include "widget.h"\n'
        "void\n"
        "Widget::render(int x, int y)\n"
        "{\n"
        "    return;\n"
        "}\n"
    ))
    write(tmp_path, "widget.h", "")
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    names = [d["name"] for d in node(graph, "widget.cpp")["defines"]]
    assert "render" in names


def test_cpp_inline_method_is_attributed_to_its_class_not_the_file(tmp_path):
    # This is the real gap reported after using the skill on nested C++
    # classes: an inline method was being captured, but flatly parented to
    # the file, with no record that it belongs to its enclosing class.
    write(tmp_path, "point.hpp", (
        "class Point\n"
        "{\n"
        "public:\n"
        "    int getX() const { return x_; }\n"
        "private:\n"
        "    int x_;\n"
        "};\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    method = node(graph, "point.hpp::Point::getX")
    assert method is not None
    assert method["type"] == "function"
    assert bg_edge_exists_defines(graph, "point.hpp::Point", "point.hpp::Point::getX")
    # and NOT a flat file->method edge
    assert not bg_edge_exists_defines(graph, "point.hpp", "point.hpp::Point::getX")


def test_cpp_constructor_with_member_initializer_list_is_detected(tmp_path):
    # Regression test for a real gap: constructors using member-initializer
    # lists (extremely common in real C++) weren't matching at all, because
    # the regex required '{' immediately after the arg list.
    write(tmp_path, "point.hpp", (
        "class Point\n"
        "{\n"
        "public:\n"
        "    Point(int x, int y) : x_(x), y_(y) {}\n"
        "private:\n"
        "    int x_, y_;\n"
        "};\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    method = node(graph, "point.hpp::Point::Point")
    assert method is not None


def test_cpp_trailing_return_type_is_detected(tmp_path):
    write(tmp_path, "math.hpp", "auto add(int a, int b) -> int {\n    return a + b;\n}\n")
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    names = [d["name"] for d in node(graph, "math.hpp")["defines"]]
    assert "add" in names


def test_cpp_nested_class_qualified_name_and_parent(tmp_path):
    write(tmp_path, "outer.hpp", (
        "class Outer\n"
        "{\n"
        "public:\n"
        "    class Inner\n"
        "    {\n"
        "    public:\n"
        "        void go() {}\n"
        "    };\n"
        "};\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    assert node(graph, "outer.hpp::Outer::Inner") is not None
    assert node(graph, "outer.hpp::Outer::Inner::go") is not None
    assert bg_edge_exists_defines(graph, "outer.hpp::Outer", "outer.hpp::Outer::Inner")
    assert bg_edge_exists_defines(graph, "outer.hpp::Outer::Inner", "outer.hpp::Outer::Inner::go")


def test_cpp_free_function_still_attributed_directly_to_file(tmp_path):
    # Non-nested functions must keep the old, simpler file->function edge.
    write(tmp_path, "util.c", "int helper(void) {\n    return 0;\n}\n")
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    assert node(graph, "util.c::helper") is not None
    assert bg_edge_exists_defines(graph, "util.c", "util.c::helper")


def test_cpp_overloaded_methods_get_distinct_node_ids(tmp_path):
    # Regression test: overloaded constructors/methods (extremely common in
    # real C++) share the same qualified name, and without disambiguation
    # their node IDs collide, silently merging distinct overloads into one
    # node in the graph.
    write(tmp_path, "widget.hpp", (
        "class Widget\n"
        "{\n"
        "public:\n"
        "    Widget() {}\n"
        "    Widget(int x) : x_(x) {}\n"
        "    Widget(int x, int y) : x_(x), y_(y) {}\n"
        "private:\n"
        "    int x_, y_;\n"
        "};\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_functions=True)
    ids = [n["id"] for n in graph["nodes"] if n.get("name") == "Widget" and n["type"] == "function"]
    assert len(ids) == 3
    assert len(set(ids)) == 3  # all distinct


# ---------- Call graph ----------

def test_calls_extraction_basic(tmp_path):
    write(tmp_path, "util.c", (
        "int helper(void) { return 1; }\n"
        "int main(void) { return helper(); }\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_calls=True)
    assert bg_edge_exists_calls(graph, "util.c::main", "util.c::helper")


def test_calls_prefers_same_file_match_over_cross_file(tmp_path):
    write(tmp_path, "a.c", "int init(void) { return 1; }\nint run(void) { return init(); }\n")
    write(tmp_path, "b.c", "int init(void) { return 2; }\n")
    graph = bg.build_graph(str(tmp_path), include_calls=True)
    assert bg_edge_exists_calls(graph, "a.c::run", "a.c::init")
    assert not bg_edge_exists_calls(graph, "a.c::run", "b.c::init")


def test_calls_resolves_unambiguous_cross_file_match(tmp_path):
    write(tmp_path, "main.c", '#include "net.h"\nint main(void) { return net_listen_init(); }\n')
    write(tmp_path, "net.h", "int net_listen_init(void);\n")
    write(tmp_path, "net.c", '#include "net.h"\nint net_listen_init(void) { return 0; }\n')
    graph = bg.build_graph(str(tmp_path), include_calls=True)
    assert bg_edge_exists_calls(graph, "main.c::main", "net.c::net_listen_init")


def test_calls_too_ambiguous_across_many_files_is_skipped(tmp_path):
    # A generic name defined in many files can't be resolved by name alone;
    # skip rather than guess wrong.
    for i in range(5):
        write(tmp_path, f"mod{i}.c", "int process(void) { return 0; }\n")
    write(tmp_path, "caller.c", "int run(void) { return process(); }\n")
    graph = bg.build_graph(str(tmp_path), include_calls=True)
    calls_out = [e for e in graph["edges"] if e["type"] == "calls" and e["source"] == "caller.c::run"]
    assert calls_out == []  # too ambiguous: neither resolved nor external


def test_calls_external_function_creates_one_shared_deduped_node(tmp_path):
    write(tmp_path, "a.c", "void one(void) { log_msg(); }\nvoid two(void) { log_msg(); }\n")
    graph = bg.build_graph(str(tmp_path), include_calls=True)
    ext_nodes = [n for n in graph["nodes"] if n["id"] == "external::log_msg"]
    assert len(ext_nodes) == 1
    assert bg_edge_exists_calls(graph, "a.c::one", "external::log_msg")
    assert bg_edge_exists_calls(graph, "a.c::two", "external::log_msg")


def test_calls_local_only_drops_external_calls(tmp_path):
    write(tmp_path, "a.c", "void one(void) { log_msg(); }\n")
    graph = bg.build_graph(str(tmp_path), include_calls=True, include_external_calls=False)
    assert not any(n["id"] == "external::log_msg" for n in graph["nodes"])
    assert not any(e["type"] == "calls" for e in graph["edges"])


def test_calls_implies_functions_even_if_not_explicitly_requested(tmp_path):
    write(tmp_path, "a.c", "void one(void) { two(); }\nvoid two(void) {}\n")
    graph = bg.build_graph(str(tmp_path), include_functions=False, include_calls=True)
    assert node(graph, "a.c::one") is not None
    assert node(graph, "a.c::two") is not None


def test_calls_repeated_in_same_function_are_counted_not_duplicated(tmp_path):
    write(tmp_path, "a.c", "void log_msg(void) {}\nvoid run(void) { log_msg(); log_msg(); log_msg(); }\n")
    graph = bg.build_graph(str(tmp_path), include_calls=True)
    matches = [e for e in graph["edges"] if e["type"] == "calls"
               and e["source"] == "a.c::run" and e["target"] == "a.c::log_msg"]
    assert len(matches) == 1
    assert matches[0]["count"] == 3


def test_calls_excludes_control_flow_keywords(tmp_path):
    write(tmp_path, "a.c", (
        "void run(int n) {\n"
        "    if (n > 0) {\n"
        "        for (int i = 0; i < n; i++) { helper(); }\n"
        "    }\n"
        "}\n"
        "void helper(void) {}\n"
    ))
    graph = bg.build_graph(str(tmp_path), include_calls=True)
    call_targets = [e["target"] for e in graph["edges"] if e["type"] == "calls" and e["source"] == "a.c::run"]
    assert call_targets == ["a.c::helper"]  # not "if" or "for"


def test_single_file_scan_mode_analyzes_only_that_file(tmp_path):
    write(tmp_path, "main.c", '#include "net.h"\nint main(void) { return net_listen_init(); }\n')
    write(tmp_path, "net.h", "int net_listen_init(void);\n")
    write(tmp_path, "net.c", "int net_listen_init(void) { return 0; }\n")
    graph = bg.build_graph(str(tmp_path / "main.c"), include_calls=True)
    assert graph["stats"]["file_count"] == 1
    assert node(graph, "net.c") is None
    # net_listen_init isn't defined within the single-file scan scope, so it's
    # honestly reported as external rather than silently omitted or guessed.
    assert bg_edge_exists_calls(graph, "main.c::main", "external::net_listen_init")


# ---------- Cross-cutting ----------

def test_ignore_dirs_excludes_node_modules_and_git(tmp_path):
    write(tmp_path, "src/a.js", "")
    write(tmp_path, "node_modules/dep/index.js", "")
    write(tmp_path, ".git/hooks/pre-commit.js", "")
    graph = bg.build_graph(str(tmp_path))
    ids = {n["id"] for n in graph["nodes"]}
    assert ids == {"src/a.js"}


def test_criticality_counts_incoming_edges(tmp_path):
    write(tmp_path, "core.py", "")
    write(tmp_path, "a.py", "import core\n")
    write(tmp_path, "b.py", "import core\n")
    graph = bg.build_graph(str(tmp_path))
    assert node(graph, "core.py")["criticality"] == 2


def test_graph_output_has_expected_top_level_shape(tmp_path):
    write(tmp_path, "a.py", "")
    graph = bg.build_graph(str(tmp_path))
    assert set(graph.keys()) == {"generated_at", "root", "scan_target", "stats", "nodes", "edges"}
    assert set(graph["stats"].keys()) == {"file_count", "edge_count", "languages"}


def test_no_crash_on_non_utf8_file(tmp_path):
    p = tmp_path / "weird.c"
    p.write_bytes(b"int main() { \xff\xfe return 0; }")
    graph = bg.build_graph(str(tmp_path))
    assert graph["stats"]["file_count"] == 1  # read_text degrades gracefully, no exception


def test_empty_repo_produces_empty_graph(tmp_path):
    graph = bg.build_graph(str(tmp_path))
    assert graph["stats"]["file_count"] == 0
    assert graph["stats"]["edge_count"] == 0
    assert graph["nodes"] == []
    assert graph["edges"] == []


def test_dashboard_embeds_valid_parseable_json(tmp_path):
    write(tmp_path, "a.py", "import b\n")
    write(tmp_path, "b.py", "")
    graph = bg.build_graph(str(tmp_path))
    html = rd.TEMPLATE.replace("__GRAPH_JSON__", json.dumps(graph))
    m = re.search(r"const GRAPH = (.*);\n", html)
    assert m is not None
    parsed = json.loads(m.group(1))
    assert parsed["stats"]["file_count"] == 2
