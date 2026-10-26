"""Writes report.json (machine-readable, used by `replay`) and report.md."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .config import Invariant, Scenario
from .models import Action, Conflict, Defect


def write_report(
    out_dir: Path,
    scenario: Scenario,
    strategy: str,
    actions: list[Action],
    conflicts: list[Conflict],
    invariants: list[Invariant],
    runs: list[dict],
    defects: list[Defect],
    warnings: list[str],
    elapsed_s: float,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "scenario": scenario.name,
        "strategy": strategy,
        "elapsed_s": round(elapsed_s, 1),
        "actions": [asdict(a) for a in actions],
        "conflicts": [
            {"id": c.id, "score": c.score, "reasons": c.reasons, "rationale": c.rationale} for c in conflicts
        ],
        "invariants": [asdict(i) for i in invariants],
        "runs": runs,
        "defects": [
            {
                "kind": d.kind,
                "name": d.name,
                "message": d.message,
                "explanation": d.explanation,
                "interleaving": asdict(d.interleaving),
                "reproduced_by": d.reproduced_by,
                "observation": asdict(d.observation),
            }
            for d in defects
        ],
        "warnings": warnings,
    }
    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    concurrent_runs = sum(1 for r in runs if r["kind"] == "concurrent")
    lines = [
        f"# SyncHunter report: {scenario.name}",
        "",
        f"- Strategy: **{strategy}**",
        f"- Actions discovered: {len(actions)}; conflict candidates: {len(conflicts)}",
        f"- Interleavings executed: {len(runs)} ({concurrent_runs} concurrent)",
        f"- Distinct defects: **{len(defects)}**",
        f"- Time: {elapsed_s:.1f} s",
        "",
        "## Defects",
        "",
    ]
    if not defects:
        lines.append("No defects found.")
    for n, d in enumerate(defects, start=1):
        lines += [
            f"### {n}. {d.kind}: {d.name}",
            "",
            f"{d.message}",
            "",
            f"- First found by interleaving `{d.interleaving.id}` ({d.interleaving.kind}): "
            f"`{d.interleaving.describe()}`",
            f"- Reproduced by {len(d.reproduced_by)} interleaving(s): {', '.join(d.reproduced_by)}",
            f"- Replay: `synchunter replay {json_path.as_posix()} --defect {n}`",
        ]
        if d.explanation:
            lines += ["", f"> {d.explanation}"]
        lines.append("")
    lines += ["## Conflict candidates", "", "| Conflict | Score | Why |", "|---|---|---|"]
    for c in conflicts:
        why = "; ".join(c.reasons) or "-"
        if c.rationale:
            why += f" (LLM: {c.rationale})"
        lines.append(f"| `{c.id}` | {c.score} | {why} |")
    if warnings:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in warnings]
    md_path = out_dir / "report.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
