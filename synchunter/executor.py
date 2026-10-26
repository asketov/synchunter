"""Executor: replays one interleaving deterministically, without any LLM in the loop.

Steps are scheduled with asyncio at exact start offsets, so two users can act within
milliseconds of each other - far faster than an LLM agent could decide.
"""
from __future__ import annotations

import asyncio

from playwright.async_api import Browser

from .browser import Sessions, http
from .config import Scenario
from .models import Interleaving, Observation, Step


async def execute(browser: Browser, scenario: Scenario, interleaving: Interleaving) -> Observation:
    await http(scenario, scenario.reset)
    async with Sessions(browser, scenario) as sessions:
        await sessions.prefill()

        async def run(step: Step) -> tuple[str, str]:
            await asyncio.sleep(step.delay_ms / 1000)
            return f"{step.user}.{step.key}", await sessions.perform(step.user, step.key)

        results = dict(await asyncio.gather(*(run(step) for step in interleaving.steps)))
        await asyncio.sleep(scenario.settle_ms / 1000)
        ui_before = await sessions.observe()
        state = await http(scenario, scenario.state)
        await sessions.reload()
        ui_after = await sessions.observe()
    return Observation(step_results=results, ui_before=ui_before, ui_after=ui_after, state=state)
