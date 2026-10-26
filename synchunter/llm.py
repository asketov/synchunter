"""Thin wrapper around the Claude API used by the Planner and the Oracle.

The LLM is optional: without it SyncHunter falls back to heuristics, so the whole
pipeline runs offline. Credentials are resolved by the Anthropic SDK
(ANTHROPIC_API_KEY or an `ant auth login` profile).
"""
from __future__ import annotations

import json
import sys

DEFAULT_MODEL = "claude-opus-5-5"


def _warn(message: str) -> None:
    print(f"[llm] {message}", file=sys.stderr)


class LLM:
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def ask_json(self, system: str, prompt: str, schema: dict, effort: str = "medium") -> dict | None:
        """One structured-output request; returns the parsed JSON or None on any failure."""
        errors = self._anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                # If the model declines, let the API retry on its recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except errors.AuthenticationError:
            _warn("no valid credentials; set ANTHROPIC_API_KEY or run `ant auth login`")
            return None
        except errors.RateLimitError:
            _warn("rate limited; continuing without the LLM for this step")
            return None
        except errors.APIStatusError as error:
            _warn(f"API error {error.status_code}: {error.message}")
            return None
        except errors.APIConnectionError:
            _warn("could not reach the API; continuing without the LLM for this step")
            return None

        if response.stop_reason == "refusal":
            _warn("the model declined this request")
            return None
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            _warn("response was not valid JSON")
            return None


def make_llm(enabled: bool, model: str = DEFAULT_MODEL) -> LLM | None:
    if not enabled:
        return None
    try:
        return LLM(model)
    except ImportError:
        _warn("the anthropic package is missing; install with: pip install -e .[llm]")
    except Exception as error:  # e.g. no credentials configured at all
        _warn(f"LLM disabled: {error}")
    return None
