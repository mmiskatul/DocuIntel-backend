import pytest


@pytest.fixture(autouse=True)
def _no_api_key_by_default(monkeypatch):
    """Tests must not depend on the developer's .env: run unauthenticated unless a test sets a key itself."""
    import app.core.security as sec
    monkeypatch.setattr(sec, "API_KEY", "")
