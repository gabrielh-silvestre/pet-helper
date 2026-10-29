import pytest


@pytest.fixture(autouse=True)
def _disable_langsmith_tracing(monkeypatch):
    # a variável legada vence LANGSMITH_TRACING em langsmith.tracing_is_enabled
    for legacy in ("LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING_V2"):
        monkeypatch.delenv(legacy, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
