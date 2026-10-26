"""Browser sessions: one isolated browser context per simulated user."""
from __future__ import annotations

import asyncio
import json
import urllib.request

from playwright.async_api import Browser, BrowserContext, Page

from .config import HttpCall, Scenario

LIST_ACTIONS_JS = """(selector) => Array.from(document.querySelectorAll(selector))
  .filter((el) => el.offsetParent !== null && !el.disabled)
  .map((el) => ({
    key: el.dataset.testid || (el.innerText || el.value || '').trim(),
    label: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim(),
  }))
  .filter((a) => a.key)"""


def _http_json(base_url: str, call: HttpCall) -> dict | None:
    data = b"" if call.method != "GET" else None
    request = urllib.request.Request(base_url + call.path, method=call.method, data=data)
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read()
    return json.loads(body) if body else None


async def http(scenario: Scenario, call: HttpCall | None) -> dict | None:
    if call is None:
        return None
    return await asyncio.to_thread(_http_json, scenario.base_url, call)


def is_reachable(scenario: Scenario) -> bool:
    try:
        urllib.request.urlopen(scenario.base_url, timeout=3).close()
        return True
    except OSError:
        return False


class Sessions:
    """Opens every user's start page in its own context; use with `async with`."""

    def __init__(self, browser: Browser, scenario: Scenario) -> None:
        self.browser = browser
        self.scenario = scenario
        self.contexts: dict[str, BrowserContext] = {}
        self.pages: dict[str, Page] = {}

    async def __aenter__(self) -> "Sessions":
        for user, path in self.scenario.users.items():
            context = await self.browser.new_context()
            page = await context.new_page()
            self.contexts[user] = context
            self.pages[user] = page
            await page.goto(self.scenario.base_url + path, wait_until="domcontentloaded")
            await self._wait_ready(page)
        return self

    async def __aexit__(self, *exc) -> None:
        for context in self.contexts.values():
            await context.close()

    async def _wait_ready(self, page: Page) -> None:
        if self.scenario.ready_selector:
            await page.wait_for_selector(self.scenario.ready_selector, state="attached", timeout=10_000)

    async def list_actions(self, user: str) -> list[dict]:
        return await self.pages[user].evaluate(LIST_ACTIONS_JS, self.scenario.action_selector)

    async def prefill(self) -> None:
        """Type scenario inputs up front so that actions later fire without delay."""
        for page in self.pages.values():
            for selector, text in self.scenario.inputs.items():
                field = page.locator(selector)
                if await field.count():
                    await field.first.fill(text)

    async def perform(self, user: str, key: str) -> str:
        page = self.pages[user]
        target = page.locator(f'[data-testid="{key}"]')
        if await target.count() == 0:
            target = page.get_by_role("button", name=key, exact=True)
        try:
            await target.first.click(timeout=2_000)
            return "ok"
        except Exception as error:  # element gone, disabled or covered
            return f"unavailable ({type(error).__name__})"

    async def observe(self) -> dict[str, dict[str, str | None]]:
        seen: dict[str, dict[str, str | None]] = {}
        for user, page in self.pages.items():
            seen[user] = {}
            for observable in self.scenario.observables:
                element = page.locator(observable.selector).first
                if await element.count() == 0:
                    value = None
                elif observable.attr:
                    value = await element.get_attribute(observable.attr)
                else:
                    value = (await element.inner_text()).strip()
                seen[user][observable.name] = value
        return seen

    async def reload(self) -> None:
        for page in self.pages.values():
            await page.reload(wait_until="domcontentloaded")
            await self._wait_ready(page)
