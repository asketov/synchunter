# SyncHunter

**LLM-guided interleaving exploration for detecting synchronization defects in multi-user web applications.**

Some bugs only appear when two people use an app at the same time: one partner leaves a pair
while the other submits an answer, two users submit within the same 100 ms, a partner keeps
seeing a status that changed long ago. Single-user test agents cannot see these bugs, and
multi-user LLM agents that act in turns (e.g. MAdroid, ICSE'26) miss races by construction.

SyncHunter combines **agents that understand what features do** with **systematic,
machine-speed replay of action orderings** and an **automatic consistency oracle**.

> Research prototype for a master's thesis (SPbPU, 2026). Status: v0.1, early prototype.

## How it works

```
 Explorer            Planner               Executor                Oracle
 (per user)   --->   (heuristic + LLM) ---> (no LLM, Playwright) --> (checks + LLM)
 runs each action    pairs of actions of     replays each pair in     compares what users see,
 alone, records      different users that    many orderings and       stale views, and invariants
 HTTP writes and     touch the same state;   timings, one browser     over server state;
 state diffs         picks interleavings     context per user         dedupes defects, reports
```

1. **Explorer** discovers every user's actions (`button[data-testid]` by default), runs each one
   alone from a reset state, and records its footprint: HTTP writes, changed state keys, touched entity ids.
2. **Planner** pairs actions of different users that both write, scores them by footprint overlap
   (shared state keys, shared entity ids, same endpoint) and, with `--llm`, re-ranks them by
   meaning using the app description. For every pair it schedules interleavings:
   - `sequential`: one user acts, then the other (turn-taking baseline);
   - `concurrent`: the second action starts 0 / 50 / 120 / 250 ms after the first, in both directions.
3. **Executor** replays each interleaving deterministically with asyncio + Playwright, one isolated
   browser context per user. No LLM in this loop, so actions can be milliseconds apart.
4. **Oracle** reports:
   - `invariant-violation`: a rule over the server state is false;
   - `cross-user-inconsistency`: after a reload, users disagree on a shared observable;
   - `stale-view`: what a user saw differs from what a fresh reload shows.

   With `--llm` it also proposes invariants from the app description (kept only if they hold on a
   healthy state sample) and explains each defect.

The key idea: **the LLM decides what to test; deterministic code decides how fast to stress it.**

## Quick start

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev,llm]"
playwright install chromium

# terminal 1: the demo app with planted defects
synchunter demo-app

# terminal 2: hunt
synchunter run scenarios/demo_couple_app.yaml --strategy sequential --out out-sequential
synchunter run scenarios/demo_couple_app.yaml --strategy interleave --out out-interleave
synchunter replay out-interleave/report.json --defect 2
```

Reports land in `out-*/report.md` (human-readable) and `out-*/report.json` (used by `replay`).

## Demo results

The bundled demo app (`demo_app/`) is a simplified couple app with three planted defects:
a lost reveal (both partners answer concurrently and the answers are never revealed), an orphan
answer (an answer is stored for a pair that the partner just left) and a stale partner view
(the client polls only every 4 s). `synchunter demo-app --fixed` runs the corrected version.

Measured on one Windows laptop, heuristic mode (no LLM):

| App | Strategy | Interleavings | Distinct defects | Race defects found | Time |
|---|---|---|---|---|---|
| buggy | sequential (turn-taking baseline) | 8 | 2 | 0 of 2 | 26 s |
| buggy | interleave | 36 | 4 | 2 of 2 | 74 s |
| fixed | interleave | 36 | 0 (no false positives) | - | - |

Turn-taking finds only the stale views. Both races need concurrent orderings and are found
by several interleavings each (e.g. `A.submit@0ms, B.submit@50ms`). Timing-dependent results can
vary between machines; `replay` re-runs the exact interleaving that exposed a defect.

## Using an LLM

```bash
export ANTHROPIC_API_KEY=...   # or `ant auth login`
synchunter run scenarios/demo_couple_app.yaml --llm --out out-llm
```

With `--llm` (Claude, default model `claude-opus-5-5`, change with `--model`):

- the Planner re-ranks conflict candidates using the app description;
- the Oracle proposes invariants and writes them to `out-llm/llm_invariants.json`; they are
  checked only with `--trust-llm-invariants`, because they are Python expressions you should review first;
- every defect in the report gets a short explanation and fix direction.

Requests use structured JSON output and the API's server-side fallback (`fallbacks: "default"`),
so a declined request is retried on Anthropic's recommended fallback model. Without `--llm`,
or if a request fails, SyncHunter continues with heuristics only.

## Testing your own app

Write a scenario YAML (see `scenarios/demo_couple_app.yaml`):

- `users`: one start page per simulated user;
- `reset` / `state`: test hooks that reset the app and dump its server state as JSON;
- `observables`: UI elements to compare across users (`shared: true`) and before/after reload;
- `invariants`: Python expressions over `state` that must always hold;
- timing: `settle_ms`, `sequential_gap_ms`, `offsets_ms`.

Mark interactive elements with `data-testid`, or set `action_selector`. Invariants are evaluated
with a reduced set of builtins, which is not a security sandbox: only use scenario files you trust.

## Project layout

```
synchunter/      explorer.py  planner.py  executor.py  oracle.py  llm.py  browser.py  report.py  cli.py
demo_app/        demo couple app with planted defects (FastAPI + one HTML page)
scenarios/       scenario files
tests/           unit tests for the planner and the oracle
```

## Roadmap

- [ ] LLM-driven Explorer that discovers multi-step scenarios (login, navigation, forms), not just buttons
- [ ] Network-level perturbations: slow responses, offline tab, reconnect
- [ ] More than two users and three-action interleavings
- [ ] Benchmark of open-source multi-user apps (Rocket.Chat, Mattermost, Excalidraw, Etherpad, Wekan)
      with injected synchronization defects
- [ ] Baselines: single-user LLM agent, MAdroid-style turn-taking ported to web, random exploration
- [ ] Evaluation of the LLM components (ranking quality, invariant precision, cost per defect)

## Related work

- MAdroid: *Breaking Single-Tester Limits: Multi-Agent LLMs for Multi-User Feature Testing*, ICSE 2026, arXiv:2506.17539
- Simian: Billes, Møller, Pradel. *Systematic Black-Box Analysis of Collaborative Web Applications*, PLDI 2017
- REINS: *Agent-guided Thread Scheduling for Concurrency Bug Detection and Reproduction*, arXiv:2610.13421
- WebTestPilot, arXiv:2602.11724; Temac, arXiv:2506.00520; GUITester, arXiv:2601.04500

## License

MIT
