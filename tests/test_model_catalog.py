from __future__ import annotations

import json
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import httpx
import pytest
from rh_cli.errors import RhCliError

from backend.app import apply_custom_input_values, configured_workflow_analysis, normalize_custom_input_values, workflow_input_catalog
from backend.model_catalog import FavoriteModelCatalog
from backend.server import LocalHandler


class Store:
    def __init__(self):
        self.records = [
            {"id": "key_a", "account_id": "a", "site": "cn", "api_type": "NORMAL", "api_key": "test-secret-a", "status": "ready"},
            {"id": "key_b", "account_id": "b", "site": "ai", "api_type": "NORMAL", "api_key": "test-secret-b", "status": "ready"},
        ]

    def keys(self):
        return self.records

    def current_account_id(self):
        return "a"

    def get_account(self, account_id):
        return {"id": account_id} if account_id in {"a", "b"} else None


def response(loras=None, diffusion=None):
    return httpx.Response(200, json={
        "LoraLoader": {"input": {"required": {"lora_name": [loras if loras is not None else ["b.safetensors", "a.safetensors", "a.safetensors", "", 1]]}}},
        "UNETLoader": {"input": {"required": {"unet_name": [diffusion if diffusion is not None else ["base.safetensors"]]}}},
    })


def test_model_catalog_is_account_scoped_cached_and_refreshable(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert url.endswith("/object_info")
        assert kwargs["follow_redirects"] is False
        return response()

    monkeypatch.setattr("backend.model_catalog.httpx.get", get)
    catalog = FavoriteModelCatalog()
    store = Store()
    first = catalog.list_models(store)
    assert first["models"] == {"lora": ["a.safetensors", "b.safetensors"], "diffusion": ["base.safetensors"]}
    assert first["source"] == "favorites" and not first["cached"]
    assert catalog.list_models(store)["cached"]
    assert len(calls) == 1
    other = catalog.list_models(store, "b")
    assert other["account_id"] == "b" and other["site"] == "ai"
    assert calls[1].startswith("https://www.runninghub.ai/proxy/")
    catalog.list_models(store, refresh=True)
    assert len(calls) == 3
    assert "test-secret" not in json.dumps(first)
    # Rotating the same account's key cannot reuse the old credentials' cache.
    store.records[0]["api_key"] = "test-rotated-secret"
    assert not catalog.list_models(store)["cached"]
    assert len(calls) == 4


def test_model_catalog_deduplicates_simultaneous_requests(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: calls.append(args[0]) or response())
    catalog = FavoriteModelCatalog()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: catalog.list_models(Store()), range(16)))
    assert len(calls) == 1
    assert all(item["models"]["diffusion"] == ["base.safetensors"] for item in results)


def test_model_catalog_expires_and_returns_independent_lists(monkeypatch):
    now = [1000]
    calls = []
    monkeypatch.setattr("backend.model_catalog.time.monotonic", lambda: now[0])
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: calls.append(1) or response([], []))
    catalog = FavoriteModelCatalog()
    first = catalog.list_models(Store())
    first["models"]["lora"].append("local-only")
    assert catalog.list_models(Store())["models"]["lora"] == []
    now[0] += 301
    assert not catalog.list_models(Store())["cached"]
    assert len(calls) == 2


def test_model_catalog_never_borrows_another_accounts_key(monkeypatch):
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: pytest.fail("must not contact RunningHub"))
    store = Store()
    with pytest.raises(RhCliError) as error:
        FavoriteModelCatalog().list_models(store, "__general__")
    assert error.value.code == "MODEL_ACCOUNT_REQUIRED"
    with pytest.raises(RhCliError) as error:
        FavoriteModelCatalog().list_models(store, "missing")
    assert error.value.code == "ACCOUNT_NOT_FOUND"
    store.records = [store.records[1]]
    with pytest.raises(RhCliError) as error:
        FavoriteModelCatalog().list_models(store, "a")
    assert error.value.code == "MODEL_KEY_REQUIRED"


@pytest.mark.parametrize("failure", ["timeout", "network", "auth", "html", "malformed", "http"])
def test_model_catalog_errors_never_expose_credential_urls(monkeypatch, failure):
    def get(url, **kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout(url)
        if failure == "network":
            raise httpx.ConnectError(url)
        if failure == "auth":
            return httpx.Response(401, text=url)
        if failure == "html":
            return httpx.Response(200, text=url)
        if failure == "http":
            return httpx.Response(502, text=url)
        return httpx.Response(200, json={"code": 404, "msg": url})

    monkeypatch.setattr("backend.model_catalog.httpx.get", get)
    with pytest.raises(RhCliError) as error:
        FavoriteModelCatalog().list_models(Store())
    assert "test-secret" not in str(error.value)
    assert "/proxy/" not in str(error.value)


def test_model_inputs_are_automatic_and_preserve_manual_configuration():
    workflow = {
        "1": {"class_type": "LoraLoaderModelOnly", "_meta": {"title": "Load LoRA"}, "inputs": {"lora_name": "old.safetensors", "strength_model": 0.7}},
        "2": {"class_type": "UNETLoader", "_meta": {"title": "Load Diffusion Model"}, "inputs": {"unet_name": "base.safetensors"}},
        "3": {"class_type": "LinkedInput", "inputs": {"lora_name": ["1", 0]}},
    }
    automatic = configured_workflow_analysis(workflow, None)
    assert [item["id"] for item in automatic["custom_inputs"]] == ["1:lora_name", "2:unet_name"]
    assert automatic["custom_inputs"][0]["label"] == "Load LoRA · lora_name"
    assert {item["model_type"] for item in workflow_input_catalog(workflow) if item.get("model_type")} == {"lora", "diffusion"}
    config = {"mode": "manual", "items": [{"id": "1:lora_name", "kind": "select", "options": ["old.safetensors"]}]}
    manual = configured_workflow_analysis(workflow, config)
    assert [item["id"] for item in manual["custom_inputs"]] == ["1:lora_name"]
    values = normalize_custom_input_values(workflow, manual, {"1:lora_name": "new-favorite.safetensors"})
    apply_custom_input_values(workflow, values)
    assert workflow["1"]["inputs"]["lora_name"] == "new-favorite.safetensors"
    assert workflow["1"]["inputs"]["strength_model"] == 0.7
    assert workflow["2"]["inputs"]["unet_name"] == "base.safetensors"


def test_model_catalog_http_api_only_returns_names(monkeypatch):
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: response())
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
    server.store = Store()
    server.manager = None
    server.model_catalog = FavoriteModelCatalog()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/models/favorites?account_id=a"
        with urllib.request.urlopen(url, timeout=5) as result:
            payload = result.read().decode()
            assert result.status == 200
        assert json.loads(payload)["models"]["lora"] == ["a.safetensors", "b.safetensors"]
        assert "test-secret" not in payload and "/proxy/" not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
