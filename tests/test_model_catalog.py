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


def resources(records=None, *, pages=1, has_next=False):
    return httpx.Response(200, json={"code": 0, "data": {
        "records": records or [], "pages": str(pages), "hasNext": has_next,
    }})


def lora_record(*versions):
    return {"resourceType": "LORA", "versions": [
        {"versionResourceName": filename, "baseModel": label} for filename, label in versions
    ]}


@pytest.fixture(autouse=True)
def resource_api(monkeypatch):
    """Every metadata request is local and synthetic, including older tests."""
    original_client = httpx.Client
    state = {"requests": [], "reply": lambda request: resources()}

    def handle(request):
        state["requests"].append(request)
        return state["reply"](request)

    monkeypatch.setattr("backend.model_catalog.httpx.Client", lambda **kwargs:
                        original_client(**kwargs, transport=httpx.MockTransport(handle)))
    return state


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


def test_model_catalog_http_api_returns_names_and_safe_metadata(monkeypatch):
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
        assert json.loads(payload)["model_metadata"]["lora"]["a.safetensors"] == {"base_model": ""}
        assert "test-secret" not in payload and "/proxy/" not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_lora_tags_match_the_exact_version_file_and_keep_submission_values(monkeypatch, resource_api):
    names = ["COS.safetensors", "models/loras/sub/style-v2.safetensors", "unlisted.safetensors"]
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: response(names, []))
    resource_api["reply"] = lambda request: resources([
        lora_record(("models/loras/COS.safetensors", "qwen-image-2.1")),
        lora_record(("models/loras/otherCOS.safetensors", "krea2")),
        lora_record(("models/loras/sub/style-v1.safetensors", "SD 1.5"),
                    ("models\\loras\\sub\\style-v2.safetensors", "Flux2-Klein-9B")),
        lora_record(("models/loras/elsewhere/unlisted.safetensors", "SDXL")),
    ])
    result = FavoriteModelCatalog().list_models(Store())
    assert result["models"]["lora"] == sorted(names, key=str.casefold)
    assert result["model_metadata"]["lora"] == {
        "COS.safetensors": {"base_model": "qwen-image-2.1"},
        "models/loras/sub/style-v2.safetensors": {"base_model": "Flux2-Klein-9B"},
        "unlisted.safetensors": {"base_model": ""},
    }
    queries = [json.loads(request.content) for request in resource_api["requests"]]
    assert {query["resourceName"] for query in queries} == {"COS.safetensors", "style-v2.safetensors", "unlisted.safetensors"}
    assert all(query["resourceType"] == "LORA" for query in queries)
    assert "test-secret" not in json.dumps(result)


def test_lora_tags_consider_later_search_pages_and_conflicting_file_names(monkeypatch, resource_api):
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: response(["style.safetensors"], []))

    def reply(request):
        page = json.loads(request.content)["current"]
        label = "qwen-image-2.1" if page == 1 else "SDXL"
        return resources([lora_record(("models/loras/style.safetensors", label))], pages=2, has_next=page == 1)

    resource_api["reply"] = reply
    result = FavoriteModelCatalog().list_models(Store())
    assert result["model_metadata"]["lora"]["style.safetensors"]["base_model"] == ""
    assert len(resource_api["requests"]) == 2


@pytest.mark.parametrize("failure", ["auth", "network", "timeout", "html", "schema", "incomplete", "secret", "url", "missing"])
def test_metadata_failures_preserve_favorites_and_never_leak_credentials(monkeypatch, resource_api, failure):
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: response(["style.safetensors"], ["base.safetensors"]))

    def reply(request):
        if failure == "network":
            raise httpx.ConnectError("Authorization: Bearer test-secret-a")
        if failure == "timeout":
            raise httpx.ReadTimeout("test-secret-a")
        if failure == "auth":
            return httpx.Response(401, text="test-secret-a")
        if failure == "html":
            return httpx.Response(200, text="test-secret-a")
        if failure == "schema":
            return httpx.Response(200, json={"code": 0, "data": "test-secret-a"})
        label = {"secret": "test-secret-a", "url": "https://example.test/proxy/key", "missing": ""}.get(failure, "SDXL")
        return resources([lora_record(("models/loras/style.safetensors", label))], pages=99, has_next=True) if failure == "incomplete" else resources([lora_record(("style.safetensors", label))])

    resource_api["reply"] = reply
    result = FavoriteModelCatalog().list_models(Store())
    assert result["models"] == {"lora": ["style.safetensors"], "diffusion": ["base.safetensors"]}
    assert result["model_metadata"]["lora"]["style.safetensors"]["base_model"] == ""
    assert "test-secret" not in json.dumps(result) and "/proxy/" not in json.dumps(result)


def test_metadata_cache_is_copied_refreshed_and_owned_by_selected_account(monkeypatch, resource_api):
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: response(["style.safetensors"], []))
    resource_api["reply"] = lambda request: resources([lora_record(("style.safetensors", "qwen-image-2.1" if request.headers["Authorization"] == "Bearer test-secret-a" else "SDXL"))])
    catalog = FavoriteModelCatalog()
    first = catalog.list_models(Store(), "a")
    first["model_metadata"]["lora"]["style.safetensors"]["base_model"] = "changed locally"
    assert catalog.list_models(Store(), "a")["model_metadata"]["lora"]["style.safetensors"]["base_model"] == "qwen-image-2.1"
    assert len(resource_api["requests"]) == 1
    other = catalog.list_models(Store(), "b")
    assert other["model_metadata"]["lora"]["style.safetensors"]["base_model"] == "SDXL"
    assert resource_api["requests"][-1].url.host == "www.runninghub.ai"
    resource_api["reply"] = lambda request: resources([lora_record(("style.safetensors", "Flux2-Klein-9B"))])
    assert catalog.list_models(Store(), "a", refresh=True)["model_metadata"]["lora"]["style.safetensors"]["base_model"] == "Flux2-Klein-9B"


def test_metadata_work_is_bounded_and_leaves_every_favorite_selectable(monkeypatch, resource_api):
    names = ["a.safetensors", "b.safetensors", "c.safetensors"]
    monkeypatch.setattr("backend.model_catalog.httpx.get", lambda *args, **kwargs: response(names, []))
    monkeypatch.setattr("backend.model_catalog.METADATA_MAX_MODELS", 2)
    result = FavoriteModelCatalog().list_models(Store())
    assert result["models"]["lora"] == names and len(resource_api["requests"]) == 2
    assert result["model_metadata"]["lora"]["c.safetensors"]["base_model"] == ""
    resource_api["requests"].clear()
    monkeypatch.setattr("backend.model_catalog.METADATA_SECONDS", 0)
    FavoriteModelCatalog().list_models(Store())
    assert not resource_api["requests"]
