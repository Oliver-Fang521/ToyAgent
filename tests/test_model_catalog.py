import json

import pytest
from fastapi.testclient import TestClient

from gaia_agent.interactive.api import create_app


def test_unknown_pair_rejected_without_network():
    from gaia_agent.interactive.catalog import ModelSelection, resolve_profile

    with pytest.raises(ValueError, match="model_unavailable"):
        resolve_profile(ModelSelection(provider="deepseek", model="gpt-6.1-sol"))


def test_catalog_has_only_two_official_hosts():
    from gaia_agent.interactive.catalog import public_catalog

    catalog = public_catalog()
    assert {p["id"] for p in catalog["providers"]} == {"openai", "deepseek"}
    assert {p["hostname"] for p in catalog["providers"]} == {"api.openai.com", "api.deepseek.com"}
    assert all(
        not m["capabilities"]["hosted_python"]
        for m in catalog["models"]
        if m["provider"] == "deepseek"
    )
    assert "api_key" not in json.dumps(catalog)


def test_anonymous_catalog_does_not_read_credentials(tmp_path):
    with TestClient(
        create_app(tmp_path, "test-service-token", execute=lambda *args: None)
    ) as client:
        assert client.get("/v1/model-catalog").status_code == 401
        headers = {"authorization": "Bearer test-service-token"}
        response = client.get("/v1/model-catalog", headers=headers)
        assert response.status_code == 200
        assert len(response.json()["models"]) >= 25
        assert client.post("/v1/model-catalog", headers=headers).status_code == 401
        assert client.get("/v1/credentials", headers=headers).status_code == 401


def test_registry_capabilities_are_per_model():
    from gaia_agent.interactive.catalog import ModelSelection, public_catalog, resolve_profile

    sol = resolve_profile(ModelSelection(provider="openai", model="gpt-6.1-sol"))
    assert sol.allowed_reasoning_efforts == ("low", "medium", "high", "xhigh", "max")
    assert sol.default_reasoning_effort == "low"
    plain = resolve_profile(ModelSelection(provider="openai", model="gpt-4.1"))
    assert plain.allowed_reasoning_efforts == ()
    assert plain.default_reasoning_effort is None
    flash = resolve_profile(ModelSelection(provider="deepseek", model="deepseek-flash"))
    assert flash.display_name == "DeepSeek-V4.1-Flash"
    assert flash.allowed_reasoning_efforts == ("none", "low", "high", "max")
    models = public_catalog()["models"]
    assert all(m["evidence_url"].startswith("https://") for m in models)
    assert not any(m["model"].startswith(("tts-", "text-embedding", "gpt-image")) for m in models)
    with pytest.raises(ValueError, match="model_unavailable"):
        resolve_profile(ModelSelection(provider="openai", model="gpt-4"))


def test_gpt_61_terra_catalog_exposes_documented_features_and_efforts():
    from gaia_agent.interactive.catalog import ModelSelection, public_catalog, resolve_profile
    from gaia_agent.interactive.models import InteractiveRequest, resolve_selection
    from gaia_agent.interactive.tool_policy import allowed_tools

    model = resolve_profile(ModelSelection(provider="openai", model="gpt-6.1-terra"))
    assert model.display_name == "GPT-6.1 Terra"
    assert model.protocol == "responses"
    assert model.allowed_reasoning_efforts == ("none", "low", "medium", "high", "xhigh", "max")
    assert model.default_reasoning_effort == "medium"
    assert model.capabilities.image and model.capabilities.structured_output
    assert model.capabilities.function_tools and model.capabilities.hosted_search
    assert "execute_python" in allowed_tools(model, set())
    assert "web_search" in allowed_tools(model, set())
    assert any(m["model"] == "gpt-6.1-terra" for m in public_catalog()["models"])
    for effort in model.allowed_reasoning_efforts:
        selected = resolve_selection(InteractiveRequest(provider="openai", model="gpt-6.1-terra", question="hello", reasoning_effort=effort))
        assert selected.reasoning_effort == effort


def test_interactive_default_is_terra_medium():
    from gaia_agent.interactive.models import InteractiveConfig, InteractiveRequest, resolve_selection
    config = InteractiveConfig(openai_api_key="synthetic")
    assert (config.model, config.reasoning_effort) == ("gpt-6.1-terra", "medium")
    request = InteractiveRequest(provider="openai", model="gpt-6.1-terra", question="hello")
    assert resolve_selection(request).reasoning_effort == "medium"
