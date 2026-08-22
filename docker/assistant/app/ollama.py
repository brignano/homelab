"""Minimal async Ollama client.

Deliberately thin: this stack sends text in and gets text out. No tool calling,
no function schemas, no retrieval. The repo already learned (twice) that a 3B on
this hardware is unreliable at tool use and at staying faithful to retrieved
sources — see docs/ai-strategy.md → Decision log. Everything factual is computed
in Python before the model ever sees it; the model only writes prose.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)


class OllamaError(Exception):
    """Generation failed — caller decides how to degrade."""


@dataclass
class Completion:
    text: str
    seconds: float
    model: str


class Ollama:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        model: str,
        num_ctx: int,
        timeout_s: int,
    ) -> None:
        self._session = session
        self._base = base_url
        self._model = model
        self._num_ctx = num_ctx
        self._timeout_s = timeout_s

    @property
    def model(self) -> str:
        return self._model

    async def available(self) -> bool:
        """Is Ollama up and does it have our model loaded?"""
        try:
            async with self._session.get(
                f"{self._base}/api/tags", timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("ollama unreachable: %s", exc)
            return False
        names = {m.get("name", "") for m in data.get("models", [])}
        return self._model in names

    async def generate(self, prompt: str, system: str, num_predict: int, temperature: float = 0.4) -> Completion:
        """One-shot, non-streaming generation.

        NOTE — `num_thread` is intentionally NOT sent. Ollama options given at
        request time override the Modelfile, and the `num_thread 4` pin in
        docker/ai/models/llama3.2.Modelfile is what stops the LXC CPU-quota
        oversubscription that once dropped generation to ~0.5 tok/s (AGENTS.md →
        "Ollama / AI tuning"). Passing a thread count here would silently undo
        it. Leave it to the Modelfile.
        """
        payload = {
            "model": self._model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {
                # Prompt evaluation is CPU-bound and roughly linear in context
                # length, so a big window costs real wall-clock even when unused.
                "num_ctx": self._num_ctx,
                # Hard stop on output length: a runaway generation would hold the
                # single worker (and 4 of 6 cores) for as long as it rambled.
                "num_predict": num_predict,
                "temperature": temperature,
            },
        }
        started = time.monotonic()
        try:
            async with self._session.post(
                f"{self._base}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout_s),
            ) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:300]
                    raise OllamaError(f"ollama returned HTTP {resp.status}: {body}")
                data = await resp.json()
        except asyncio.TimeoutError:
            raise OllamaError(f"generation exceeded {self._timeout_s}s") from None
        except aiohttp.ClientError as exc:
            raise OllamaError(f"cannot reach ollama at {self._base}: {exc}") from exc

        text = (data.get("response") or "").strip()
        if not text:
            raise OllamaError("ollama returned an empty response")
        return Completion(text=text, seconds=time.monotonic() - started, model=self._model)
