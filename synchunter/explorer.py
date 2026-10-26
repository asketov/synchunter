"""Explorer: discovers each user's actions and the footprint every action leaves.

Every action is run once, alone, from a freshly reset app. While it runs we record
the HTTP writes it sends and diff the server state before and after. The Planner
later uses these footprints to guess which actions of different users interfere.
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable
from urllib.parse import urlparse

from playwright.async_api import Browser, Request

from .browser import Sessions, http
from .config import Scenario
from .models import Action


def _items(value) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {json.dumps(v, sort_keys=True) for v in values}


def _collect_ids(obj, out: set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if (key == "id" or key.endswith("_id")) and isinstance(value, (int, str)):
                out.add(str(value))
            else:
                _collect_ids(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _collect_ids(value, out)


def diff_state(before: dict | None, after: dict | None) -> tuple[list[str], list[str]]:
    """Return (changed top-level keys, ids of the entities that were added, removed or modified)."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return [], []
    keys: list[str] = []
    ids: set[str] = set()
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        keys.append(key)
        for item in _items(before.get(key)) ^ _items(after.get(key)):
            _collect_ids(json.loads(item), ids)
    return keys, sorted(ids)


async def explore(browser: Browser, scenario: Scenario, log: Callable[[str], None] = print) -> tuple[list[Action], dict | None]:
    """Return the discovered actions and a sample of the healthy initial server state."""
    await http(scenario, scenario.reset)
    sample_state = await http(scenario, scenario.state)
    async with Sessions(browser, scenario) as sessions:
        discovered = {user: await sessions.list_actions(user) for user in scenario.users}

    actions: list[Action] = []
    for user, items in discovered.items():
        seen_keys: set[str] = set()
        for item in items:
            if item["key"] in seen_keys:
                continue
            seen_keys.add(item["key"])
            action = Action(user=user, key=item["key"], label=item["label"])

            await http(scenario, scenario.reset)
            async with Sessions(browser, scenario) as sessions:
                await sessions.prefill()
                before = await http(scenario, scenario.state)
                writes: list[str] = []

                def on_request(request: Request) -> None:
                    if request.method != "GET" and request.resource_type in ("fetch", "xhr"):
                        writes.append(f"{request.method} {urlparse(request.url).path}")

                sessions.pages[user].on("request", on_request)
                result = await sessions.perform(user, item["key"])
                await asyncio.sleep(scenario.settle_ms / 1000)
                after = await http(scenario, scenario.state)

            action.writes = sorted(set(writes))
            action.state_keys, action.entity_ids = diff_state(before, after)
            actions.append(action)
            log(f"  {action.id:<22} {result:<12} writes={action.writes} changes={action.state_keys}")
    return actions, sample_state
