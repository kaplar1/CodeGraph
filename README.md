# CodeGraph

**Codebase Knowledge Graph** — a [Claude Skill](https://docs.claude.com) that maps a codebase into a machine-readable graph and a shareable interactive dashboard — using static analysis, not an LLM, so it's free to re-run every session and never goes stale.

Built for two audiences at once: **teammates**, who get a visual, clickable map of the architecture instead of a walkthrough meeting, and **Claude**, who can query a compact JSON graph instead of re-reading dozens of files every time it needs to remember how a repo is structured.

## What it produces

Running it against a repo generates three artifacts, no LLM calls involved:

| File | For | What it is |
|---|---|---|
| `knowledge-graph.json` | Claude / tooling | Files, functions, classes, and their import/dependency edges |
| `ARCHITECTURE.md` | Quick orientation | Short summary — most depended-on files, language mix |
| `dashboard.html` | Teammates | Self-contained, searchable, force-directed graph. No server needed |

## Quick start

Clone or copy this repo into `.claude/skills/codegraph/` at the root of your project and ask Claude Code to map the codebase — or run it directly:

```bash
python3 scripts/build_graph.py . --out .knowledge-graph
python3 scripts/render_dashboard.py .knowledge-graph/knowledge-graph.json --out .knowledge-graph/dashboard.html
```

Add `--functions` to also map individual functions/classes (for C/C++, this properly nests member functions under their enclosing class rather than flattening everything under the file):

```bash
python3 scripts/build_graph.py . --out .knowledge-graph --functions
```

Add `--calls` to also map function-to-function call edges (C/C++), including calls out to external/library functions like `sigemptyset` or `fprintf` — resolvable calls point at the real definition, everything else is grouped by library into a shared node (e.g. `external::stdio`, `external::signal`), with the actual function names called kept on the edge. Point it at a single file instead of a directory to keep a call graph readable:

```bash
python3 scripts/build_graph.py src/main.c --out .knowledge-graph --calls
```

## Language support

| Language | Import resolution | Function/class extraction |
|---|---|---|
| Python | Relative + `src/`-layout absolute imports | Top-level, flat |
| C / C++ | Quoted + angle-bracket `#include`, header-suffix index | Full nesting: class/struct/namespace hierarchy, overload-safe |
| JavaScript / TypeScript | Relative imports | Top-level, flat |
| Go, Java, Ruby, Rust | Lighter heuristics | Top-level, flat |

## Project layout

```
CodeGraph/
├── SKILL.md              # what Claude reads to use this automatically
├── scripts/
│   ├── build_graph.py     # scan → extract → resolve → JSON + Markdown
│   ├── render_dashboard.py
│   └── smoke_test.py      # run against a real repo before deploying
├── tests/                 # pytest regression suite
├── evals/                 # trigger-eval prompts for Claude Code
└── TESTING.md
```

## Testing

```bash
pip install pytest --break-system-packages
pytest tests/ -v
```

See [`TESTING.md`](./TESTING.md) for the full testing story, including the smoke test to run against your actual repo before rolling this out to a team.

## Known limitations

Regex-based static analysis, not a real parser — it's a strong orientation aid, not ground truth. Full details, per-language caveats, and validated real-repo results (Redis, nlohmann/json, Flask) are in [`SKILL.md`](./SKILL.md).
