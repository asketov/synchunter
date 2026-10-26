"""A tiny couple app (a simplified Twekle) used as a test subject for SyncHunter.

Two partners, A and B, form a pair. Each partner answers the Question of the Day;
the answers are revealed to both only after both partners have answered. A partner
can leave the pair, which deletes the pair together with its answers.

In buggy mode (the default) the app contains three planted defects:

1. Lost reveal (race): /api/answer reads "has my partner answered?" *before* its
   slow write. If both partners submit at nearly the same time, both read "no",
   and the pair is never revealed.
2. Orphan answer (race): /api/answer finds the pair, then writes slowly. If the
   partner leaves the pair in between, the answer is stored for a pair that no
   longer exists.
3. Stale partner view: the client polls every 4 s, so the partner keeps seeing an
   outdated status long after the server state changed.

Only defect 3 is visible when users act strictly one after another; defects 1 and
2 need concurrent interleavings. Run with --fixed to get the corrected version.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import itertools
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
WRITE_DELAY_S = 0.2  # simulated slow database write


def initial_state() -> dict:
    return {
        "question": {"id": 1, "text": "What made you smile today?"},
        "pairs": [{"id": 1, "members": ["A", "B"], "revealed": False}],
        "answers": [],
    }


class Body(BaseModel):
    user: str
    text: str = ""


def create_app(buggy: bool = True) -> FastAPI:
    app = FastAPI(title="SyncHunter demo couple app")
    state = initial_state()
    answer_ids = itertools.count(100)
    lock = asyncio.Lock()
    poll_ms = 4000 if buggy else 300

    def pair_of(user: str) -> dict | None:
        return next((p for p in state["pairs"] if user in p["members"]), None)

    def answers_of(pair_id: int) -> list[dict]:
        return [a for a in state["answers"] if a["pair_id"] == pair_id]

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML.replace("__POLL_MS__", str(poll_ms))

    @app.get("/api/me")
    def me(user: str) -> dict:
        pair = pair_of(user)
        if pair is None:
            return {"user": user, "paired": False}
        answers = answers_of(pair["id"])
        return {
            "user": user,
            "paired": True,
            "partner": next(m for m in pair["members"] if m != user),
            "pair_id": pair["id"],
            "answered": any(a["user"] == user for a in answers),
            "revealed": pair["revealed"],
            "answers": answers if pair["revealed"] else [],
        }

    async def save_answer(body: Body) -> dict:
        pair = pair_of(body.user)
        if pair is None:
            raise HTTPException(409, "You are not in a pair")
        answers = answers_of(pair["id"])
        if any(a["user"] == body.user for a in answers):
            raise HTTPException(409, "You have already answered")
        partner_done = any(a["user"] != body.user for a in answers)  # defect 1 when unlocked
        await asyncio.sleep(WRITE_DELAY_S)
        # Defect 2 when unlocked: the pair may have been deleted during the sleep.
        state["answers"].append(
            {"id": next(answer_ids), "pair_id": pair["id"], "user": body.user, "text": body.text}
        )
        if partner_done:
            pair["revealed"] = True
        return {"ok": True}

    @app.post("/api/answer")
    async def answer(body: Body) -> dict:
        if buggy:
            return await save_answer(body)
        async with lock:
            return await save_answer(body)

    def remove_pair(user: str) -> dict:
        pair = pair_of(user)
        if pair is None:
            raise HTTPException(409, "You are not in a pair")
        state["pairs"].remove(pair)
        state["answers"] = [a for a in state["answers"] if a["pair_id"] != pair["id"]]
        return {"ok": True}

    @app.post("/api/pair/leave")
    async def leave(body: Body) -> dict:
        if buggy:
            return remove_pair(body.user)
        async with lock:
            return remove_pair(body.user)

    # Test hooks used by SyncHunter: reset to a known state and dump the full state.
    @app.post("/__reset")
    def reset() -> dict:
        state.clear()
        state.update(initial_state())
        return {"ok": True}

    @app.get("/api/state")
    def dump_state() -> dict:
        return copy.deepcopy(state)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SyncHunter demo couple app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--fixed", action="store_true", help="run the version without planted defects")
    args = parser.parse_args()

    import uvicorn

    mode = "fixed" if args.fixed else "buggy"
    print(f"Demo app ({mode}) on http://{args.host}:{args.port}  - open /?user=A and /?user=B")
    uvicorn.run(create_app(buggy=not args.fixed), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
