"""Command line: `synchunter run`, `synchunter replay`, `synchunter demo-app`."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .config import Invariant, load_scenario
from .llm import DEFAULT_MODEL, make_llm
from .models import Defect, Interleaving, Step


async def run_campaign(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    from .browser import is_reachable
    from .executor import execute
    from .explorer import explore
    from .oracle import check, explain, propose_invariants
    from .planner import find_conflicts, interleavings, rank_with_llm
    from .report import write_report

    scenario = load_scenario(args.scenario)
    if not is_reachable(scenario):
        print(f"Cannot reach {scenario.base_url}. Start the app first, e.g.: synchunter demo-app")
        return 2
    llm = make_llm(args.llm, args.model)
    started = time.monotonic()
    warnings: list[str] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)
        try:
            print("[1/4] Explorer: discovering actions")
            actions, sample_state = await explore(browser, scenario)

            print("[2/4] Planner: finding conflicting actions")
            conflicts = find_conflicts(actions)
            if llm:
                conflicts = rank_with_llm(llm, scenario, conflicts)
            for conflict in conflicts:
                print(f"  score {conflict.score}  {conflict.id}")

            invariants = list(scenario.invariants)
            if llm:
                proposed = propose_invariants(llm, scenario, sample_state)
                out = Path(args.out)
                out.mkdir(parents=True, exist_ok=True)
                (out / "llm_invariants.json").write_text(
                    json.dumps([asdict(i) for i in proposed], indent=2), encoding="utf-8"
                )
                print(f"  LLM proposed {len(proposed)} invariant(s) -> {out / 'llm_invariants.json'}")
                if args.trust_llm_invariants:
                    invariants += proposed

            schedule = [
                il for n, c in enumerate(conflicts, start=1) for il in interleavings(n, c, scenario, args.strategy)
            ][: args.budget]
            print(f"[3/4] Executor + Oracle: {len(schedule)} interleavings ({args.strategy})")

            found: dict[tuple[str, str], Defect] = {}
            runs: list[dict] = []
            for interleaving in schedule:
                observation = await execute(browser, scenario, interleaving)
                defects = check(scenario, invariants, interleaving, observation, warnings)
                for defect in defects:
                    first = found.setdefault(defect.key, defect)
                    first.reproduced_by.append(interleaving.id)
                runs.append({
                    "id": interleaving.id,
                    "kind": interleaving.kind,
                    "steps": interleaving.describe(),
                    "results": observation.step_results,
                    "defects": [f"{d.kind}:{d.name}" for d in defects],
                })
                marks = ", ".join(f"{d.kind}:{d.name}" for d in defects) or "ok"
                print(f"  {interleaving.id:<6} {interleaving.kind:<10} {interleaving.describe():<55} {marks}")
        finally:
            await browser.close()

    defects = list(found.values())
    if llm:
        for defect in defects:
            defect.explanation = explain(llm, scenario, defect)

    print("[4/4] Report")
    json_path, md_path = write_report(
        Path(args.out), scenario, args.strategy, actions, conflicts, invariants,
        runs, defects, sorted(set(warnings)), time.monotonic() - started,
    )
    print(f"  {len(defects)} distinct defect(s) in {len(runs)} runs")
    for n, defect in enumerate(defects, start=1):
        print(f"  {n}. {defect.kind}: {defect.name} (first: {defect.interleaving.id}, {defect.interleaving.kind})")
    print(f"  -> {md_path}\n  -> {json_path}")
    return 1 if defects and args.fail_on_defect else 0


async def replay(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    from .executor import execute
    from .oracle import check

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    entry = report["defects"][args.defect - 1]
    raw = entry["interleaving"]
    interleaving = Interleaving(raw["id"], raw["conflict_id"], raw["kind"], [Step(**s) for s in raw["steps"]])
    scenario = load_scenario(args.scenario)
    invariants = [Invariant(**i) for i in report["invariants"]]

    print(f"Replaying {entry['kind']}: {entry['name']} via {interleaving.describe()}")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)
        try:
            observation = await execute(browser, scenario, interleaving)
        finally:
            await browser.close()
    defects = check(scenario, invariants, interleaving, observation)
    reproduced = any((d.kind, d.name) == (entry["kind"], entry["name"]) for d in defects)
    print("Reproduced." if reproduced else "Not reproduced on this run (timing-dependent defects can be flaky).")
    for defect in defects:
        print(f"  {defect.kind}: {defect.name}: {defect.message}")
    return 0 if reproduced else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synchunter", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="explore, plan, execute interleavings and report defects")
    run.add_argument("scenario", help="path to a scenario YAML file")
    run.add_argument("--strategy", choices=["sequential", "interleave"], default="interleave",
                     help="sequential = turn-taking baseline; interleave = also concurrent orderings")
    run.add_argument("--budget", type=int, default=200, help="maximum number of interleavings to execute")
    run.add_argument("--out", default="out", help="output directory for reports")
    run.add_argument("--llm", action="store_true", help="use Claude for planning, invariants and explanations")
    run.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model id (default {DEFAULT_MODEL})")
    run.add_argument("--trust-llm-invariants", action="store_true",
                     help="also check LLM-proposed invariants (they are Python expressions; review them first)")
    run.add_argument("--headed", action="store_true", help="show the browsers")
    run.add_argument("--fail-on-defect", action="store_true", help="exit with code 1 when defects are found")

    rep = sub.add_parser("replay", help="re-run the interleaving that exposed a reported defect")
    rep.add_argument("report", help="path to report.json")
    rep.add_argument("--defect", type=int, default=1, help="defect number from the report (1-based)")
    rep.add_argument("--scenario", default="scenarios/demo_couple_app.yaml")
    rep.add_argument("--headed", action="store_true")

    sub.add_parser("demo-app", help="start the bundled demo app (add --fixed for the corrected version)",
                   add_help=False)

    args, rest = parser.parse_known_args(argv)
    if args.command == "demo-app":
        from demo_app.app import main as demo_main

        sys.argv = ["demo-app", *rest]
        demo_main()
        return 0
    if rest:
        parser.error(f"unrecognized arguments: {' '.join(rest)}")
    if args.command == "run":
        return asyncio.run(run_campaign(args))
    return asyncio.run(replay(args))


if __name__ == "__main__":
    sys.exit(main())
