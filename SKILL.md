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
