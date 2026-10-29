import pytest


@pytest.fixture(autouse=True)
def _disable_langsmith_tracing(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
