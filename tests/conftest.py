import hashlib

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.repositories.conversations import InMemoryConversationRepository
from app.services.llm import LLMResult


class MockLLMClient:
    """Fully offline LLM mock — no external calls in tests."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    async def generate(
        self, prompt: str, model: str | None = None, mode: str = "direct"
    ) -> LLMResult:
        self.calls.append({"prompt": prompt, "model": model})
        if self.fail:
            raise RuntimeError("simulated provider outage")
        return LLMResult(
            text="mocked response",
            model=model or "mock-model",
            provider="mock",
            prompt_tokens=3,
            completion_tokens=5,
            total_tokens=8,
        )


# API key used across the test suite; API_KEY_HASH is its SHA-256 digest.
TEST_API_KEY = "test-api-key"
TEST_API_KEY_HASH = hashlib.sha256(TEST_API_KEY.encode()).hexdigest()


@pytest.fixture(autouse=True)
def test_settings(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("API_KEY_HASH", TEST_API_KEY_HASH)
    # Generous limit so functional tests never trip it; rate-limit tests override.
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1000")
    monkeypatch.setenv("ALLOWED_MODELS", '["custom-model"]')
    monkeypatch.setenv("REQUIRE_LLM_KEYS", "false")
    # Tests never touch a real database unless they opt in explicitly.
    monkeypatch.setenv("REPOSITORY_BACKEND", "memory")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest.fixture
def repo() -> InMemoryConversationRepository:
    return InMemoryConversationRepository()


def make_client(llm, repo) -> TestClient:
    app = create_app()
    app.state.llm_client = llm
    app.state.repository = repo
    return TestClient(app, raise_server_exceptions=False, headers={"X-API-Key": TEST_API_KEY})


@pytest.fixture
def client(mock_llm: MockLLMClient, repo: InMemoryConversationRepository) -> TestClient:
    return make_client(mock_llm, repo)


@pytest.fixture
def failing_client(repo: InMemoryConversationRepository) -> TestClient:
    return make_client(MockLLMClient(fail=True), repo)
