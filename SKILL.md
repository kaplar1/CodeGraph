---
name: codegraph
description: Generate a machine-readable knowledge graph (JSON) and a shareable interactive HTML dashboard of this codebase, mapping files, functions/classes, and their import/dependency relationships. Use when the user asks to map, visualize, document, or understand the codebase's architecture, wants to onboard a teammate, wants a diagram of module dependencies, or wants Claude to reference the codebase's structure without re-reading every file.
---

# CodeGraph — Codebase Knowledge Graph

Builds a dependency/structure graph of the current repo using deterministic
static analysis (regex-based import/definition extraction — no LLM calls, so
it's fast and free to re-run) and renders it two ways:

1. `knowledge-graph.json` — machine-readable graph (nodes = files/functions/
   classes, edges = imports + defines), for Claude or other tools to query
   instead of re-reading source files.
2. `ARCHITECTURE.md` — a short human/AI-readable summary (file counts,
   language breakdown, most depended-on files). **Read this file first**
   before diving into the full JSON or source — it's the cheapest way to
   orient in a new session.
3. `dashboard.html` — a single self-contained interactive visualization
   (force-directed graph, searchable, click-to-inspect). No server needed;
   safe to open locally or share directly with teammates (e.g. via Slack,
   or host it as a static file).

## Usage

Generate the graph (file-level only, fast, default):

```
python3 .claude/skills/codegraph/scripts/build_graph.py . --out .knowledge-graph
```

Include function/class-level nodes too (bigger, more detailed graph). For
C/C++, this now properly nests member functions under their enclosing
class/struct/namespace — e.g. `file.hpp::Widget::render` with a `defines`
edge from `file.hpp::Widget`, not flattened directly under the file — so
the graph reflects real class structure, not just a flat function list:

```
python3 .claude/skills/codegraph/scripts/build_graph.py . --out .knowledge-graph --functions
```

Render the visual dashboard from the JSON:

```
python3 .claude/skills/codegraph/scripts/render_dashboard.py .knowledge-graph/knowledge-graph.json --out .knowledge-graph/dashboard.html
```

### Call graph (C/C++)

Add `--calls` to also extract **who calls what** inside function bodies —
e.g. `main.c::main` → `net.c::net_listen_init`, or `main.c::main` →
`external::stdio` for calls to library/OS functions that aren't defined
anywhere in the scanned code (implies `--functions`):

```
python3 .claude/skills/codegraph/scripts/build_graph.py . --out .knowledge-graph --calls
```

Repeated calls to the same function collapse into one edge with a `count`,
not one edge per call site. Resolution prefers a same-file match first
(the common case for a local helper), falls back to an unambiguous
cross-file match, and skips resolution entirely (rather than guessing)
when a name is defined identically in more than a few files.

Calls that don't resolve to anything defined in the scanned code are
grouped **by library, not by function** — a function that calls both
`printf` and `fprintf` gets one `calls` edge to a single `external::stdio`
node, not two edges to two per-function nodes, since one function calling
several names from the same header is the common case and one-node-per-
function would otherwise dominate the graph. That edge carries a
`functions` list (each called name plus its own call count) so the detail
is still there, just consolidated rather than exploded into separate
edges. Library grouping is a small best-effort table of common C/POSIX
standard-library functions (stdio, stdlib, string, unistd, pthread,
signal, socket, math, time, ctype); anything not in that table falls back
to a single shared `external::other` node rather than guessing a library.
Pass `--calls-local-only` to drop external calls entirely and show only
calls that resolve to a function actually defined in the scanned code.

**This can get big fast** — a full call graph across an entire real C
codebase (Redis, ~217 files) produces over 30,000 edges. Two ways to keep
it manageable:

1. **Scope the scan to one file** instead of the whole repo — point
   `build_graph.py` at a single source file instead of a directory.
   Anything that file calls but doesn't itself define (whether a true
   external library call or just a function defined elsewhere in the repo
   you didn't include in this scan) is honestly reported as `external`,
   the same way it would be for a real library call:

   ```
   python3 .claude/skills/codegraph/scripts/build_graph.py src/main.c --out .knowledge-graph --calls
   ```

2. **Use the dashboard's focus control** — click any node and use the
   "Focus neighborhood" buttons (1 / 2 / 3 hops / All) to dim everything
   outside that radius. The full graph stays in the JSON either way
   (so Claude can still query all of it); this only affects what's
   visually emphasized.

After running, read `.knowledge-graph/ARCHITECTURE.md` and summarize the
findings for the user (most central files, language mix, size). Offer to
open or share `dashboard.html`.

## When answering architecture questions in this repo

If `.knowledge-graph/knowledge-graph.json` exists and looks reasonably fresh,
prefer querying it (e.g. with a short Python/jq snippet — filter nodes/edges
by id, language, or criticality) over grepping or opening many source files.
Only open actual source files for implementation detail the graph doesn't
capture (the graph tracks structure, not logic).

## Keeping it fresh

Re-run `build_graph.py` after significant changes — it's cheap (no LLM
calls, pure static parsing) so it's fine to re-run per session or wire into
a pre-commit/CI step that regenerates `.knowledge-graph/` and commits it.

## Testing before you deploy this

See `TESTING.md` in this skill's directory: a regression suite
(`tests/`, run with `pytest tests/ -v`), a smoke test to run against your
real repo before wide rollout (`scripts/smoke_test.py`), and a trigger-eval
set (`evals/trigger-eval.json`) for checking the skill actually fires at
the right moments in Claude Code.

## Known limitations

- Import/definition extraction is regex-based per language, not a full
  parser — it covers common patterns well, but will miss dynamic imports
  and unusual syntax. Treat the graph as a strong orientation aid, not
  ground truth.
- Only imports that resolve to files inside the repo become edges; external
  package imports are intentionally dropped to keep the graph readable.
- Language coverage: Python has the most accurate resolution (handles
  relative imports and `src/`-layout packages). C/C++ resolves `#include`
  via the including file's directory, the repo root, and a header-suffix
  index (so `<mylib/util.h>` matches `src/mylib/util.h`), and with
  `--functions` properly nests member functions under their enclosing
  class/struct/namespace (constructors with member-initializer lists and
  trailing return types are both handled; overloaded methods get distinct
  node IDs rather than colliding). JS/TS resolves relative imports only
  (bare package imports are treated as external, correctly). Go/Java/Ruby/
  Rust use lighter, flat heuristics — nested/member functions there are
  still attributed directly to the file, not to their enclosing type.
- C/C++ macros that expand to a function definition, and multi-line
  preprocessor conditionals wrapping a declaration, can still confuse the
  extraction heuristic. Namespaces declared through a macro (as some
  codebases do for ABI-versioning, e.g. `NLOHMANN_JSON_NAMESPACE_BEGIN`
  instead of a literal `namespace nlohmann {`) aren't recognized as scope,
  so their contents fall back to file-level attribution. Validated against
  real repos (Redis for C, nlohmann/json for C++, including its class
  hierarchy and 200+ overloaded/nested methods) with spot-checked output.
- `--calls` (C/C++ only) resolves by bare name, not real type-aware
  overload resolution — a call is matched against whichever function(s)
  with that name are visible in the scanned scope, preferring a same-file
  match. A name that's ambiguous across several files is skipped rather
  than guessed. Call-site detection can't distinguish a real function call
  from a function-style macro invocation or an unusual C++ functional
  cast; both are still shown, since either way something identifiable is
  being invoked at that point in the code.
- The function → library table behind external-call grouping is a small
  hand-picked list of common C/POSIX standard-library names, not a real
  header/symbol database — a third-party library's functions will land in
  the catch-all `external::other` node alongside anything else unmapped,
  rather than getting their own library node.
