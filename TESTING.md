# Testing this skill before deploying it

There are two genuinely different things to test here, and they need
different methods:

1. **The script** (`build_graph.py` / `render_dashboard.py`) — this is
   where the actual risk lives. It's regex-based static analysis, and
   during development it produced two real bugs against real repos
   (comment text swallowing a genuine function definition; a fix that
   accidentally blanked the quoted paths inside `#include`). Test this
   like you'd test any parser: synthetic fixtures + real-world runs.
2. **The skill's triggering** — whether Claude actually reaches for this
   skill at the right moments in Claude Code. This is a different failure
   mode (not "wrong output", but "never ran at all") and needs a
   different test method (eval prompts, not unit tests).

## 1. Run the regression suite

```
pip install pytest --break-system-packages   # if you don't have it
pytest tests/ -v
```

21 tests, synthetic fixtures, no network needed, runs in well under a
second. Several of these encode the exact bugs found during development
(see the comments in `tests/test_build_graph.py`) — keep them passing
before changing any extraction regex or resolution logic. It's easy to
fix one language and silently break another; that's exactly what these
catch.

If you extend language coverage yourself (Go, Rust, etc. currently use
lighter heuristics — see SKILL.md), add fixtures for the new cases the
same way: a small synthetic file tree, one assertion for what should
resolve, one for what shouldn't.

## 2. Run the smoke test against your actual repo

Unit tests use tiny synthetic snippets; they can't catch things that only
show up at real scale (unusual layout, path aliases, monorepo quirks,
runaway timing). Run:

```
python3 scripts/smoke_test.py /path/to/your/repo
```

It reports file/edge counts, flags zero-edge results (usually means
import resolution isn't matching your repo's layout, not that your repo
genuinely has no dependencies), flags a suspiciously dominant "most
central file" (could mean everything is mis-resolving to one wrong
target), checks timing, and does a courtesy scan for secret-shaped
strings in file/function names before you hand a dashboard to teammates
(the graph never embeds file *contents*, only names, so this is a light
check — but worth doing before wide sharing).

**Do this on a scratch copy of your repo, or an unpushed branch — not
directly against a shared branch — the first time**, so a bad resolution
run doesn't leave anyone staring at a wrong architecture picture.

Spot-check the output yourself too: open `ARCHITECTURE.md` and see if the
"most depended-on files" actually match what you know to be your core
modules. That's the fastest real signal of whether extraction is working
for your specific codebase.

## 3. Test whether the skill actually triggers

This is a separate concern from output correctness. `evals/trigger-eval.json`
has 18 realistic prompts (10 should trigger the skill, 8 are deliberate
near-misses that shouldn't). Anthropic's built-in `skill-creator` skill
(available in Claude Code) has a description-optimization loop built
exactly for this:

```
python -m scripts.run_loop \
  --eval-set evals/trigger-eval.json \
  --skill-path .claude/skills/codegraph \
  --model <your-model-id> \
  --max-iterations 5
```

If you'd rather not run the automated loop, it's still worth manually
trying a handful of the `should_trigger: true` prompts in a fresh Claude
Code session in your repo and confirming the skill actually fires —
especially phrasings that don't say "knowledge graph" outright (e.g. "map
out the architecture for me"), since under-triggering is the more common
failure mode than over-triggering.

## 4. Staged rollout

1. Try it yourself on a throwaway branch first.
2. Read the generated `ARCHITECTURE.md` — does it match your mental model
   of the codebase?
3. Open `dashboard.html` — does the graph look navigable, not a single
   dense hairball? (If it does look like a hairball, `--functions` is
   probably too granular for a first pass — try the default file-level
   graph.)
4. Get one teammate to try it before committing `.claude/skills/` for
   the whole team — a second set of eyes catches "this doesn't match
   reality" faster than more automated checks will.
5. Only then commit the skill (and optionally the generated
   `.knowledge-graph/` output) to a shared branch.

## 5. Keep it honest over time

Static analysis drifts from reality more slowly than an LLM-generated
summary would, but it still drifts. Re-run `smoke_test.py` after any
change to the extraction regexes, and consider re-running `build_graph.py`
on a schedule (e.g. weekly, or on merge to main) rather than treating the
first graph as permanent.
