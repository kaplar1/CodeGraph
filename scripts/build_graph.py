#!/usr/bin/env python3
"""
build_graph.py — Build a machine-readable knowledge graph of a codebase.

Deterministic, no LLM calls: walks the repo, extracts per-file imports and
top-level function/class definitions with regex, resolves internal imports
into edges, and scores files by how many other files depend on them.

Outputs:
  <out>/knowledge-graph.json   — full graph (nodes, edges, stats)
  <out>/ARCHITECTURE.md        — compact human/AI-readable summary

Usage:
  python3 build_graph.py [root_dir_or_file] [--out .knowledge-graph] [--functions] [--calls] [--calls-local-only]

  --functions          include function/class-level nodes
  --calls              also extract call edges between functions (implies --functions).
                        Includes calls to external/library functions (e.g. libc, systemd)
                        as shared "external" nodes by default.
  --calls-local-only    with --calls, only include call edges that resolve to a function
                        defined within the scanned scope; drop external/unresolved calls
  root_dir_or_file      a directory (scans recursively, as before) or a single source
                        file (scans just that file — useful for keeping a call graph
                        readable; anything it calls that isn't defined in that one file
                        is treated as external, same as a real external library call)
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "target", "vendor", ".mypy_cache",
    ".pytest_cache", "coverage", ".idea", ".vscode", "out", "bin", "obj",
    ".understand-anything", ".knowledge-graph",
}

LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go",
    ".java": "java", ".rb": "ruby", ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".h++": "cpp", ".ipp": "cpp",
    ".cs": "csharp", ".php": "php", ".kt": "kotlin", ".swift": "swift",
}

# (regex, group_index) pairs per language for import-like statements
IMPORT_PATTERNS = {
    "python": [
        (re.compile(r'^\s*from\s+([.\w]+)\s+import', re.M), 1),
        (re.compile(r'^\s*import\s+([.\w]+)', re.M), 1),
    ],
    "javascript": [
        (re.compile(r'''import\s+(?:[\w*{}\s,]+\s+from\s+)?['"](.+?)['"]'''), 1),
        (re.compile(r'''require\(\s*['"](.+?)['"]\s*\)'''), 1),
    ],
    "typescript": [
        (re.compile(r'''import\s+(?:[\w*{}\s,]+\s+from\s+)?['"](.+?)['"]'''), 1),
        (re.compile(r'''require\(\s*['"](.+?)['"]\s*\)'''), 1),
    ],
    "go": [
        (re.compile(r'"([\w./-]+)"'), 1),
    ],
    "java": [
        (re.compile(r'^\s*import\s+(?:static\s+)?([\w.]+)\s*;', re.M), 1),
    ],
    "ruby": [
        (re.compile(r'''require(?:_relative)?\s+['"](.+?)['"]'''), 1),
    ],
    "rust": [
        (re.compile(r'^\s*use\s+([\w:]+)', re.M), 1),
    ],
}
_C_INCLUDE_PATTERNS = [
    (re.compile(r'^\s*#\s*include\s*"(.+?)"', re.M), 1),
    (re.compile(r'^\s*#\s*include\s*<(.+?)>', re.M), 1),
]
IMPORT_PATTERNS["c"] = _C_INCLUDE_PATTERNS
IMPORT_PATTERNS["cpp"] = _C_INCLUDE_PATTERNS

DEF_PATTERNS = {
    "python": [
        (re.compile(r'^\s*def\s+(\w+)', re.M), "function"),
        (re.compile(r'^\s*class\s+(\w+)', re.M), "class"),
    ],
    "javascript": [
        (re.compile(r'^\s*(?:export\s+)?function\s+(\w+)', re.M), "function"),
        (re.compile(r'^\s*(?:export\s+)?class\s+(\w+)', re.M), "class"),
        (re.compile(r'^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(?.*?\)?\s*=>', re.M), "function"),
    ],
    "typescript": [
        (re.compile(r'^\s*(?:export\s+)?function\s+(\w+)', re.M), "function"),
        (re.compile(r'^\s*(?:export\s+)?class\s+(\w+)', re.M), "class"),
        (re.compile(r'^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(?.*?\)?\s*=>', re.M), "function"),
        (re.compile(r'^\s*(?:export\s+)?interface\s+(\w+)', re.M), "interface"),
    ],
    "go": [
        (re.compile(r'^\s*func\s+(?:\([^)]*\)\s*)?(\w+)', re.M), "function"),
        (re.compile(r'^\s*type\s+(\w+)\s+struct', re.M), "struct"),
    ],
    "java": [
        (re.compile(r'^\s*(?:public|private|protected)?\s*(?:static\s+)?class\s+(\w+)', re.M), "class"),
    ],
    "ruby": [
        (re.compile(r'^\s*def\s+(\w+)', re.M), "function"),
        (re.compile(r'^\s*class\s+(\w+)', re.M), "class"),
    ],
    "rust": [
        (re.compile(r'^\s*(?:pub\s+)?fn\s+(\w+)', re.M), "function"),
        (re.compile(r'^\s*(?:pub\s+)?struct\s+(\w+)', re.M), "struct"),
    ],
}

# C/C++ function-definition heuristic: zero or more "type-ish" tokens
# (constructors/destructors have none), then an optional "Class::"
# qualifier, then the function name — guarded by a keyword exclusion so a
# bare "if (...) {" / "catch (...) {" isn't misread as a zero-return-type
# function — then a parenthesized arg list with no ';' inside (rules out
# prototypes/declarations and calls, and rejects for-loops via their
# internal ';'), then optional const/override/noexcept modifiers, an
# optional trailing return type (`-> T`), an optional constructor
# member-initializer list (`: a(1), b(2)`), then an opening '{'.
# Character classes like [^;{}] intentionally match newlines, so multi-line
# signatures (return type / args on separate lines) are still matched.
_C_KEYWORDS_NOT_FUNCTIONS = (
    r'if|for|while|switch|catch|return|sizeof|else|do|new|delete|throw|typedef'
)
_C_FUNC_DEF_RE = re.compile(
    r'(?:^|\n)[ \t]*(?:[\w<>,\*&~\[\]]+[ \t\n]+)*(?:[\w:]+::)?'
    r'(?!(?:' + _C_KEYWORDS_NOT_FUNCTIONS + r')\b)'
    r'(~?\w+)\s*'
    r'\([^;{}]*\)\s*(?:const\s*)?(?:override\s*)?(?:final\s*)?'
    r'(?:noexcept(?:\([^)]*\))?\s*)?'
    r'(?:->\s*[^{;:]+?\s*)?'      # trailing return type: auto f() -> int {
    r'(?:\:\s*[^{;]+?\s*)?'       # ctor member-init list: Foo() : a(1), b(2) {
    r'\{'
)
_C_STRUCT_RE = re.compile(r'^\s*(?:typedef\s+)?struct\s+(\w+)', re.M)
_CPP_CLASS_RE = re.compile(r'^\s*(?:template\s*<[^>]*>\s*)?class\s+(\w+)\b(?!\s*[=,>])', re.M)

# Call-site heuristic: a bare identifier immediately followed by '(' —
# e.g. "sigemptyset(&ss)" inside a function body. This is deliberately
# permissive (it can't tell a real call from a function-style cast or a
# macro invocation) but excludes control-flow keywords and common
# primitive/type keywords that would otherwise read as calls to
# themselves (e.g. "if (x)", "sizeof(x)", "int(x)" as a C++ cast).
_CALL_SITE_RE = re.compile(r'\b([A-Za-z_]\w*)\s*\(')
_CALL_EXCLUDE_WORDS = frozenset("""
    if for while switch catch return sizeof else do new delete throw typedef
    int char float double void long short unsigned signed bool
    struct union enum const static extern inline volatile register auto goto
    case default break continue alignof decltype noexcept explicit template
    typename using namespace class public private protected friend virtual
    operator this defined
""".split())

# Best-effort C/POSIX standard-library function -> library name table, used
# to group unresolved external calls by library instead of one node per
# function (e.g. printf + fprintf both land on external::stdio). Not
# exhaustive by design — anything not listed here falls back to a single
# shared external::other bucket rather than guessing.
_KNOWN_LIBRARY_FUNCTIONS = {
    **{n: "stdio" for n in """
        printf fprintf sprintf snprintf vprintf vfprintf vsnprintf
        scanf fscanf sscanf fopen fclose fread fwrite fgets fputs fputc fgetc
        getchar putchar puts gets perror fflush remove rename tmpfile
        feof ferror rewind fseek ftell
    """.split()},
    **{n: "stdlib" for n in """
        malloc calloc realloc free exit abort atoi atol atof
        strtol strtoul strtod rand srand qsort bsearch getenv setenv system
    """.split()},
    **{n: "string" for n in """
        strcpy strncpy strcat strncat strcmp strncmp strlen strchr strrchr
        strstr strtok memcpy memmove memset memcmp strdup
    """.split()},
    **{n: "unistd" for n in """
        read write close open fork execve execvp execv pipe dup dup2
        sleep usleep getpid getppid unlink access
    """.split()},
    **{n: "pthread" for n in """
        pthread_create pthread_join pthread_mutex_init pthread_mutex_lock
        pthread_mutex_unlock pthread_mutex_destroy pthread_cond_init
        pthread_cond_wait pthread_cond_signal pthread_cond_broadcast
        pthread_exit pthread_detach pthread_cancel
    """.split()},
    **{n: "signal" for n in """
        signal sigaction sigemptyset sigaddset sigdelset sigismember
        sigprocmask kill raise sigwait sigsuspend
    """.split()},
    **{n: "socket" for n in """
        socket bind listen accept connect send recv sendto recvfrom
        setsockopt getsockopt shutdown htons htonl ntohs ntohl
    """.split()},
    **{n: "math" for n in """
        sin cos tan asin acos atan atan2 sqrt pow log log10 log2 exp
        fabs floor ceil round fmod
    """.split()},
    **{n: "time" for n in """
        time difftime mktime localtime gmtime strftime clock gettimeofday
        nanosleep
    """.split()},
    **{n: "ctype" for n in """
        isalpha isdigit isspace isupper islower isalnum ispunct iscntrl
        toupper tolower
    """.split()},
}


def extract_call_names(text):
    """Count call-like identifier(...) occurrences in a block of text
    (expected to already be comment/string-stripped). Returns a name -> count
    Counter so repeated calls to the same function collapse to one edge with
    a count, rather than one edge per call site."""
    counts = Counter()
    for m in _CALL_SITE_RE.finditer(text):
        name = m.group(1)
        if name not in _CALL_EXCLUDE_WORDS:
            counts[name] += 1
    return counts


def _walk_brace_body(text, body_start):
    """Given the position just after an opening '{', walk forward counting
    brace balance and return the position of the matching '}' (or len(text)
    if the braces never balance, e.g. malformed/truncated input)."""
    depth = 1
    i = body_start
    n = len(text)
    while i < n and depth > 0:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return i - 1 if depth == 0 else n

# Scope-opener variants of the above: these additionally require finding the
# real opening '{' (non-greedily, so they land on the *nearest* one — the
# scope's own body, not some unrelated later brace) and are used only to
# compute body spans for attributing nested functions to their enclosing
# class/struct/namespace. The lighter regexes above (without the brace
# requirement) remain what's used for simple flat name extraction, since
# forward declarations like "class Foo;" have no body but are still worth
# recording as a name.
_SCOPE_NAMESPACE_RE = re.compile(r'^\s*namespace\s+(\w+)\s*\{', re.M)
_SCOPE_STRUCT_RE = re.compile(r'^\s*(?:typedef\s+)?struct\s+(\w+)[^{;]*?\{', re.M)
_SCOPE_CLASS_RE = re.compile(r'^\s*(?:template\s*<[^>]*>\s*)?class\s+(\w+)\b(?!\s*[=,>])[^{;]*?\{', re.M)

DEF_PATTERNS["c"] = [
    (_C_FUNC_DEF_RE, "function"),
    (_C_STRUCT_RE, "struct"),
]
DEF_PATTERNS["cpp"] = [
    (_C_FUNC_DEF_RE, "function"),
    (_C_STRUCT_RE, "struct"),
    (_CPP_CLASS_RE, "class"),
]

# Comment text can accidentally look like code to the def/import regexes
# above (e.g. a comment like "the article (foo, bar) says..." reads as a
# plausible function signature, and can even swallow the real function that
# follows since ')' inside the comment satisfies the closing-paren
# requirement). Strip block/line comments before extracting anything.
# String/char literal *contents* are only blanked before def-extraction
# (not import-extraction, since #include "path.h" needs its quotes intact).
_C_COMMENT_RE = re.compile(r'/\*.*?\*/|//[^\n]*', re.S)
_C_STRING_CHAR_RE = re.compile(r'"(?:\\.|[^"\\\n])*"' r"|'(?:\\.|[^'\\\n])*'")


def strip_c_comments(text):
    def repl(m):
        s = m.group(0)
        return "\n" * s.count("\n") if s.startswith("/*") else ""
    return _C_COMMENT_RE.sub(repl, text)


def strip_c_strings(text):
    return _C_STRING_CHAR_RE.sub(lambda m: m.group(0)[0] + m.group(0)[-1], text)


def iter_source_files(root, ignore_dirs):
    if os.path.isfile(root):
        if os.path.splitext(root)[1] in LANG_BY_EXT:
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext in LANG_BY_EXT:
                yield os.path.join(dirpath, fn)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def extract_imports(text, lang):
    found = []
    for pattern, group in IMPORT_PATTERNS.get(lang, []):
        for m in pattern.finditer(text):
            found.append(m.group(group))
    return found


def extract_defs(text, lang):
    found = []
    for pattern, kind in DEF_PATTERNS.get(lang, []):
        for m in pattern.finditer(text):
            found.append((m.group(1), kind))
    return found


def _find_scope_spans(text):
    """Find class/struct/namespace bodies with character-offset spans, by
    locating each opener's brace and then walking forward counting brace
    balance to find its match. This is what lets a nested method be
    attributed to the class that contains it, rather than flatly to the
    file — a plain per-line regex has no notion of nesting at all."""
    openers = []
    for pattern, kind in (
        (_SCOPE_NAMESPACE_RE, "namespace"),
        (_SCOPE_CLASS_RE, "class"),
        (_SCOPE_STRUCT_RE, "struct"),
    ):
        for m in pattern.finditer(text):
            body_start = m.end()  # just after the opening '{'
            body_end = _walk_brace_body(text, body_start)
            openers.append({"name": m.group(1), "kind": kind, "start": body_start, "end": body_end})
    openers.sort(key=lambda o: o["start"])
    return openers


def _enclosing_chain(pos, scopes, exclude=None):
    """Scopes (outer→inner) whose body contains `pos`, excluding `exclude` itself."""
    chain = [s for s in scopes if s is not exclude and s["start"] <= pos < s["end"]]
    chain.sort(key=lambda s: s["start"])
    return chain


def extract_defs_with_scope(text, lang):
    """Like extract_defs, but for C/C++ additionally resolves each
    function/class/struct to its qualified name (e.g. "Outer::inner") and
    immediate parent, by tracking brace-nesting scope spans. Returns a list
    of dicts: name, kind, qualified, parent (qualified name of the
    enclosing class/struct/namespace, or None if file-level)."""
    if lang not in ("c", "cpp"):
        return [{"name": n, "kind": k, "qualified": n, "parent": None,
                 "body_start": None, "body_end": None}
                for n, k in extract_defs(text, lang)]

    scopes = _find_scope_spans(text)
    results = []

    for s in scopes:
        parents = _enclosing_chain(s["start"] - 1, scopes, exclude=s)
        qualified = "::".join([p["name"] for p in parents] + [s["name"]])
        parent_q = "::".join(p["name"] for p in parents) if parents else None
        results.append({"name": s["name"], "kind": s["kind"], "qualified": qualified, "parent": parent_q,
                         "body_start": None, "body_end": None})

    for m in _C_FUNC_DEF_RE.finditer(text):
        pos = m.start(1)
        chain = _enclosing_chain(pos, scopes)
        qualified = "::".join([p["name"] for p in chain] + [m.group(1)])
        parent_q = "::".join(p["name"] for p in chain) if chain else None
        body_start = m.end()  # right after the opening '{', since the pattern ends with a literal '{'
        body_end = _walk_brace_body(text, body_start)
        results.append({"name": m.group(1), "kind": "function", "qualified": qualified, "parent": parent_q,
                         "body_start": body_start, "body_end": body_end})

    return results


def build_python_module_index(root, file_index):
    """Map dotted-path suffixes (e.g. 'flask.helpers', 'helpers') -> repo-relative file,
    so absolute imports resolve correctly under src/-style layouts."""
    index = defaultdict(list)
    for rel in file_index:
        if not rel.endswith(".py"):
            continue
        no_ext = rel[:-3]
        parts = no_ext.split("/")
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            index[suffix].append(rel)
    return index


def build_header_index(file_index):
    """Map path suffixes (e.g. 'mylib/util.h', 'util.h') -> repo-relative file,
    so angle-bracket includes resolve even when the header lives under an
    include/ or src/ root rather than next to the including file."""
    index = defaultdict(list)
    header_exts = (".h", ".hpp", ".hh", ".hxx", ".h++")
    for rel in file_index:
        if os.path.splitext(rel)[1] not in header_exts:
            continue
        parts = rel.split("/")
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            index[suffix].append(rel)
    return index


def resolve_import(raw_import, lang, file_dir, root, file_index, py_module_index=None, header_index=None):
    """Best-effort resolution of an import string to a repo-relative file path."""
    candidates = []
    if lang == "python":
        m = re.match(r'^(\.*)(.*)$', raw_import)
        dots, rest = m.group(1), m.group(2)
        if dots:
            level = len(dots)
            base_dir = file_dir
            for _ in range(level - 1):
                base_dir = os.path.dirname(base_dir)
            if rest:
                rel_path = os.path.join(base_dir, *rest.split("."))
                norm = os.path.normpath(rel_path)
                rel = os.path.relpath(norm, root)
                for c in (rel + ".py", os.path.join(rel, "__init__.py")):
                    if c.replace(os.sep, "/") in file_index:
                        return c.replace(os.sep, "/")
            else:
                rel = os.path.relpath(base_dir, root)
                c = os.path.join(rel, "__init__.py").replace(os.sep, "/")
                if c in file_index:
                    return c
            return None
        else:
            matches = (py_module_index or {}).get(rest)
            if matches:
                return sorted(matches, key=len)[0]
            return None
    elif lang in ("javascript", "typescript"):
        if raw_import.startswith("."):
            base = os.path.normpath(os.path.join(file_dir, raw_import))
            for ext in (".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.ts"):
                candidates.append(base + ext if not base.endswith(ext) else base)
        else:
            return None  # external package
    elif lang in ("c", "cpp"):
        raw = raw_import.replace("\\", "/")
        # 1. relative to the including file's own directory (typical for "quoted" includes)
        rel_candidate = os.path.normpath(os.path.join(file_dir, raw))
        rel_from_dir = os.path.relpath(rel_candidate, root).replace(os.sep, "/")
        if rel_from_dir in file_index:
            return rel_from_dir
        # 2. relative to the project root (e.g. #include "include/foo.h" from anywhere)
        if raw in file_index:
            return raw
        # 3. suffix match against all headers (handles <mylib/util.h> matching
        #    src/mylib/util.h or include/mylib/util.h, and #include "util.h"
        #    matching a header that lives elsewhere in the tree)
        matches = (header_index or {}).get(raw)
        if matches:
            return sorted(matches, key=len)[0]
        return None
    elif lang == "go":
        candidates.append(raw_import)
    elif lang == "java":
        candidates.append(raw_import.replace(".", "/") + ".java")
    elif lang == "ruby":
        candidates.append(raw_import + ".rb")
    elif lang == "rust":
        parts = raw_import.split("::")
        candidates.append(os.path.join(*parts) + ".rs")

    for c in candidates:
        norm = os.path.normpath(os.path.join(root, c)) if not os.path.isabs(c) else c
        rel = os.path.relpath(norm, root).replace(os.sep, "/")
        if rel in file_index:
            return rel
    return None


def build_graph(root, include_functions=False, include_calls=False, include_external_calls=True):
    if include_calls:
        include_functions = True  # calls are meaningless without function nodes to attach them to

    root = os.path.abspath(root)
    scan_root = root if os.path.isdir(root) else os.path.dirname(root)
    files = list(iter_source_files(root, DEFAULT_IGNORE_DIRS))
    file_index = {os.path.relpath(p, scan_root).replace(os.sep, "/") for p in files}

    nodes = []
    edges = []
    indegree = defaultdict(int)
    lang_counts = defaultdict(int)

    file_records = []
    for path in files:
        rel = os.path.relpath(path, scan_root).replace(os.sep, "/")
        ext = os.path.splitext(path)[1]
        lang = LANG_BY_EXT.get(ext, "unknown")
        text = read_text(path)
        loc = text.count("\n") + 1 if text else 0
        if lang in ("c", "cpp"):
            comment_free = strip_c_comments(text)
            imports = extract_imports(comment_free, lang)
            text_for_defs = strip_c_strings(comment_free)
            defs = extract_defs_with_scope(text_for_defs, lang)
        else:
            imports = extract_imports(text, lang)
            text_for_defs = text
            defs = extract_defs_with_scope(text, lang)
        lang_counts[lang] += 1
        file_records.append((rel, lang, loc, imports, defs, text_for_defs))

    name_index = defaultdict(list)   # bare function name -> [{"node_id":, "rel":}]
    call_sources = []                # functions with a body span, to scan for call sites later

    for rel, lang, loc, imports, defs, text_for_defs in file_records:
        nodes.append({
            "id": rel, "type": "file", "language": lang, "loc": loc,
            "defines": [{"name": d["name"], "kind": d["kind"]} for d in defs],
        })
        if include_functions:
            seen_ids = defaultdict(int)
            for d in defs:
                base_id = f"{rel}::{d['qualified']}"
                seen_ids[base_id] += 1
                node_id = base_id if seen_ids[base_id] == 1 else f"{base_id}#{seen_ids[base_id]}"
                nodes.append({
                    "id": node_id, "type": d["kind"], "language": lang,
                    "file": rel, "name": d["name"],
                })
                if d["parent"]:
                    edges.append({"source": f"{rel}::{d['parent']}", "target": node_id, "type": "defines"})
                else:
                    edges.append({"source": rel, "target": node_id, "type": "defines"})

                if d["kind"] == "function":
                    name_index[d["name"]].append({"node_id": node_id, "rel": rel})
                    if include_calls and d["body_start"] is not None:
                        call_sources.append({
                            "node_id": node_id, "rel": rel,
                            "body": text_for_defs[d["body_start"]:d["body_end"]],
                        })

    py_module_index = build_python_module_index(root, file_index)
    header_index = build_header_index(file_index)

    for rel, lang, loc, imports, defs, text_for_defs in file_records:
        file_dir = os.path.dirname(os.path.join(scan_root, rel))
        seen = set()
        for raw in imports:
            target = resolve_import(raw, lang, file_dir, scan_root, file_index, py_module_index, header_index)
            if target and target != rel and target not in seen:
                edges.append({"source": rel, "target": target, "type": "imports"})
                indegree[target] += 1
                seen.add(target)

    if include_calls:
        AMBIGUOUS_CAP = 3  # same name defined in more than this many files -> too generic to guess, skip
        external_seen = set()
        for src in call_sources:
            call_counts = extract_call_names(src["body"])
            # Calls that don't resolve to any function defined in the scanned
            # code are grouped by library (e.g. printf + fprintf -> stdio)
            # so a function that calls several names from the same library
            # gets one edge to one library node, not one edge per function
            # name.
            external_by_library = defaultdict(list)
            for called_name, count in call_counts.items():
                candidates = name_index.get(called_name, [])
                same_file = [c for c in candidates if c["rel"] == src["rel"]]
                if same_file:
                    targets = [c["node_id"] for c in same_file]
                elif candidates and len(candidates) <= AMBIGUOUS_CAP:
                    targets = [c["node_id"] for c in candidates]
                elif candidates:
                    targets = []  # too many same-named candidates across files to guess honestly
                else:
                    targets = []
                if not targets and candidates == []:
                    if include_external_calls:
                        library = _KNOWN_LIBRARY_FUNCTIONS.get(called_name, "other")
                        external_by_library[library].append((called_name, count))
                    continue
                for t in targets:
                    edges.append({"source": src["node_id"], "target": t, "type": "calls", "count": count})
            for library, calls in external_by_library.items():
                ext_id = f"external::{library}"
                if ext_id not in external_seen:
                    nodes.append({"id": ext_id, "type": "external", "name": library})
                    external_seen.add(ext_id)
                edges.append({
                    "source": src["node_id"], "target": ext_id, "type": "calls",
                    "count": sum(c for _, c in calls),
                    "functions": [{"name": n, "count": c} for n, c in sorted(calls)],
                })

    for n in nodes:
        if n["type"] == "file":
            n["criticality"] = indegree.get(n["id"], 0)

    stats = {
        "file_count": len(file_records),
        "edge_count": len(edges),
        "languages": dict(lang_counts),
    }
    graph = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": scan_root,
        "scan_target": root,
        "stats": stats,
        "nodes": nodes,
        "edges": edges,
    }
    return graph


def write_architecture_md(graph, out_path):
    file_nodes = [n for n in graph["nodes"] if n["type"] == "file"]
    top = sorted(file_nodes, key=lambda n: n.get("criticality", 0), reverse=True)[:15]
    lines = []
    lines.append("# Codebase Architecture Summary (auto-generated)\n")
    lines.append(f"Generated: {graph['generated_at']}\n")
    s = graph["stats"]
    lines.append(f"- Files analyzed: {s['file_count']}")
    lines.append(f"- Dependency edges found: {s['edge_count']}")
    lang_line = ", ".join(f"{k}: {v}" for k, v in sorted(s["languages"].items(), key=lambda x: -x[1]))
    lines.append(f"- Languages: {lang_line}\n")
    lines.append("## Most depended-on files (highest criticality)\n")
    for n in top:
        lines.append(f"- `{n['id']}` — depended on by {n.get('criticality', 0)} file(s), {n['loc']} lines")
    lines.append("\n## How to use this file")
    lines.append(
        "Read this summary first for orientation. For structural queries "
        "(who imports X, what does file Y define), query `knowledge-graph.json` "
        "instead of reading source files directly. Only open actual source files "
        "when you need implementation detail this graph doesn't capture."
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".", help="directory to scan, or a single source file")
    ap.add_argument("--out", default=".knowledge-graph")
    ap.add_argument("--functions", action="store_true", help="include function/class-level nodes")
    ap.add_argument("--calls", action="store_true", help="extract call edges between functions (implies --functions)")
    ap.add_argument("--calls-local-only", action="store_true",
                     help="with --calls, drop calls to anything outside the scanned scope instead of showing them as external nodes")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    graph = build_graph(
        args.root,
        include_functions=args.functions,
        include_calls=args.calls,
        include_external_calls=not args.calls_local_only,
    )

    json_path = os.path.join(args.out, "knowledge-graph.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)

    md_path = os.path.join(args.out, "ARCHITECTURE.md")
    write_architecture_md(graph, md_path)

    print(f"Analyzed {graph['stats']['file_count']} files, {graph['stats']['edge_count']} edges.")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Next: python3 {os.path.join(os.path.dirname(__file__), 'render_dashboard.py')} {json_path} --out {args.out}/dashboard.html")


if __name__ == "__main__":
    sys.exit(main())
