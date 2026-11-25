import asyncio
import re

import httpx
import pytest

from companion.config import Settings
from companion.gateway import create_app
from companion.providers import Completion, Provider, ProviderError, ProviderRegistry


class FakeProvider(Provider):
    """Answers arithmetic prompts correctly, and judges by reading the
    'Output to grade' section, so tests run real code paths without network."""

    def __init__(self, name="fake", paid=True, fail_on: str | None = None):
        super().__init__(name=name, label="Fake", kind="fake", base_url="http://fake", api_key="k", paid=paid)
        self.fail_on = fail_on
        self.calls = 0

    async def list_models(self):
        return ["fake-small", "fake-large"]

    async def complete(self, model, messages, system="", max_tokens=1024):
        self.calls += 1
        prompt = messages[-1]["content"]
        if self.fail_on and self.fail_on in prompt:
            raise ProviderError("boom", retryable=False)
        if system.startswith("You are an impartial evaluator"):
            graded = prompt.split("Output to grade:\n", 1)[1]
            ref = re.search(r"Reference answer:\n(.*?)\n\n", prompt, re.S)
            ok = ref is not None and ref.group(1).strip() in graded
            return Completion('{"score": %d, "reason": "checked"}' % ok, 50, 10, 1.0)
        if "Reply with JSON only" in prompt:
            return Completion('{"verdict": "approve", "feedback": "good"}', 20, 5, 1.0)
        m = re.search(r"(\d+)\s*\+\s*(\d+)", prompt)
        text = str(int(m.group(1)) + int(m.group(2))) if m else f"draft for: {prompt[:30]}"
        if model == "fake-wrong" and m:
            text = "0"
        return Completion(text, 100, 20, 2.0)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(tmp_path))
    return Settings(home=tmp_path, port=8799, _env_file=None)


@pytest.fixture
def fake():
    return FakeProvider()


@pytest.fixture
async def gw(settings, fake):
    registry = ProviderRegistry(settings)
    registry.register(fake)
    registry.pricing["fake-small"] = (1.0, 2.0)
    app = create_app(settings, registry)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1",
                                     headers={"Authorization": f"Bearer {settings.gateway_token()}"}) as client:
            client.core = app.state.core  # type: ignore[attr-defined]
            client.bridge = app.state.bridge  # type: ignore[attr-defined]
            yield client


async def wait_for(client, execution_id, states=("succeeded", "failed", "cancelled", "waiting"), timeout=10):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        ex = (await client.get(f"/api/executions/{execution_id}")).json()
        if ex["status"] in states:
            return ex
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"execution stuck in {ex['status']}: {ex}")
        await asyncio.sleep(0.05)


MATH = [{"id": f"q{i}", "input": f"What is {i} + {i}?", "expected": str(2 * i)} for i in range(1, 6)]
