import pytest

from app.core.config import Settings


def test_fail_fast_in_prod_without_keys(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        Settings(app_env="prod", _env_file=None)


def test_fail_fast_when_required_flag_set():
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        Settings(app_env="dev", require_llm_keys=True, openrouter_api_key="k", _env_file=None)


def test_changeme_placeholder_counts_as_missing():
    with pytest.raises(ValueError):
        Settings(
            app_env="prod",
            openrouter_api_key="changeme",
            gemini_api_key="changeme",
            _env_file=None,
        )


def test_prod_disables_docs():
    s = Settings(app_env="prod", openrouter_api_key="k1", gemini_api_key="k2", _env_file=None)
    assert s.docs_enabled is False


def test_dev_enables_docs():
    s = Settings(app_env="dev", _env_file=None)
    assert s.docs_enabled is True
