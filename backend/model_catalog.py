"""Read account-owned ComfyUI model choices without submitting a task."""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

import httpx
from rh_cli.errors import RhCliError
from rh_cli.http import get_site_config


MODEL_FIELDS = {"lora_name": "lora", "unet_name": "diffusion"}
MODEL_NODES = {"lora": ("LoraLoader", "lora_name"), "diffusion": ("UNETLoader", "unet_name")}
CACHE_SECONDS = 300
METADATA_SECONDS = 12
METADATA_MAX_MODELS = 64
METADATA_MAX_PAGES = 3


def model_input_type(field: str, value: Any) -> str:
    return MODEL_FIELDS.get(field, "") if isinstance(value, str) else ""


def _select_key(store: Any, requested_account: str) -> dict[str, Any]:
    account_id = str(requested_account or store.current_account_id() or "").strip()
    keys = [key for key in store.keys() if key.get("api_key")]
    if account_id and account_id != "__general__":
        if not store.get_account(account_id):
            raise RhCliError("ACCOUNT_NOT_FOUND", "找不到模型列表所属账号。")
        keys = [key for key in keys if str(key.get("account_id") or "") == account_id]
    elif len({(key.get("account_id", ""), key.get("site", "ai")) for key in keys}) > 1:
        raise RhCliError("MODEL_ACCOUNT_REQUIRED", "请先选择账号或将工作流绑定到账号，再读取收藏模型。")
    if not keys:
        raise RhCliError("MODEL_KEY_REQUIRED", "此账号尚未配置 API Key，可以先手动输入模型名称。")
    # Personal keys reflect the user's own favorites. Never borrow another
    # account's key, even if the selected account's request fails.
    keys.sort(key=lambda key: (key.get("api_type") not in {"", "NORMAL"}, key.get("status") != "ready"))
    return keys[0]


def _fetch_models(key: dict[str, Any]) -> dict[str, list[str]]:
    secret = str(key["api_key"])
    host = get_site_config(str(key.get("site") or "ai"))["api_host"]
    # RunningHub supports the complete object_info route, but not the
    # per-node ComfyUI routes. Keep only model choices from its large reply.
    # The browser never sees this credential-bearing URL.
    url = f"{host}/proxy/{quote(secret, safe='')}/object_info"
    try:
        response = httpx.get(url, timeout=30, follow_redirects=False)
        if response.status_code in {401, 403}:
            raise RhCliError("MODEL_AUTH_FAILED", "无法读取收藏模型，请检查此账号的 API Key。")
        if response.status_code != 200:
            raise RhCliError("MODEL_LIST_UNAVAILABLE", f"收藏模型服务暂不可用（HTTP {response.status_code}），可以手动输入或重试。")
        payload = response.json()
        models = {}
        for model_type, (node, field) in MODEL_NODES.items():
            choices = payload[node]["input"]["required"][field][0]
            if not isinstance(choices, list):
                raise ValueError("Invalid model choices")
            models[model_type] = sorted({value for value in choices if isinstance(value, str) and value.strip() and secret not in value}, key=str.casefold)
    except RhCliError:
        raise
    except httpx.TimeoutException:
        raise RhCliError("MODEL_LIST_TIMEOUT", "读取收藏模型超时，可以手动输入或重试。") from None
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
        # httpx exceptions contain the URL and therefore the API key.
        raise RhCliError("MODEL_LIST_UNAVAILABLE", "暂时无法读取收藏模型，可以手动输入或重试。") from None
    return models


def _lora_path(value: str) -> str:
    return value.replace("\\", "/").removeprefix("models/loras/")


def _fetch_lora_tag(client: httpx.Client, name: str, secret: str, deadline: float) -> str:
    """Only label an exact version file after checking every search-result page."""
    path = _lora_path(name)
    labels: set[str] = set()
    try:
        for page in range(1, METADATA_MAX_PAGES + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ""
            response = client.post("/openapi/v2/resource/list", json={
                "resourceType": "LORA", "resourceName": path.rsplit("/", 1)[-1],
                "current": page, "size": 50,
            }, timeout=min(6, remaining))
            if response.status_code != 200:
                return ""
            payload = response.json()
            if payload.get("code") != 0:
                return ""
            data = payload["data"]
            records = data["records"]
            if not isinstance(records, list):
                return ""
            for record in records:
                if not isinstance(record, dict) or record.get("resourceType") != "LORA":
                    continue
                for version in record.get("versions") or []:
                    if not isinstance(version, dict):
                        continue
                    filename = version.get("versionResourceName")
                    if not isinstance(filename, str) or _lora_path(filename) != path:
                        continue
                    label = version.get("baseModel")
                    if (not isinstance(label, str) or not label.strip() or len(label) > 128 or
                            secret in label or "://" in label or "/proxy/" in label or
                            any(ord(char) < 32 for char in label)):
                        return ""
                    labels.add(label.strip())
            # RunningHub returns pagination counts as strings in some replies.
            if data.get("hasNext") is True or int(data.get("pages") or 0) > page:
                continue
            if "hasNext" not in data and not data.get("pages"):
                return ""
            return next(iter(labels)) if len(labels) == 1 else ""
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        # Metadata is optional. Never expose remote messages or credential URLs,
        # and never prevent choosing a favorite when metadata cannot be read.
        return ""
    return ""


def _fetch_metadata(key: dict[str, Any], models: dict[str, list[str]]) -> dict[str, dict[str, dict[str, str]]]:
    names = models["lora"]
    metadata = {"lora": {name: {"base_model": ""} for name in names}}
    if not names:
        return metadata
    secret = str(key["api_key"])
    host = get_site_config(str(key.get("site") or "ai"))["api_host"]
    deadline = time.monotonic() + METADATA_SECONDS
    with httpx.Client(base_url=host, headers={"Authorization": f"Bearer {secret}"}, follow_redirects=False) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            queried = names[:METADATA_MAX_MODELS]
            labels = pool.map(lambda name: _fetch_lora_tag(client, name, secret, deadline), queried)
            for name, label in zip(queried, labels):
                metadata["lora"][name]["base_model"] = label
    return metadata


class FavoriteModelCatalog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self._fetch_locks: dict[tuple, threading.Lock] = {}

    def list_models(self, store: Any, account_id: str = "", *, refresh: bool = False) -> dict[str, Any]:
        key = _select_key(store, account_id)
        scope = (str(key.get("account_id") or ""), str(key.get("site") or "ai"),
                 hashlib.sha256(str(key["api_key"]).encode()).hexdigest())
        with self._lock:
            fetch_lock = self._fetch_locks.setdefault(scope, threading.Lock())
        with fetch_lock:
            with self._lock:
                cached = self._cache.get(scope)
            cache_hit = bool(not refresh and cached and time.monotonic() - cached[0] < CACHE_SECONDS)
            if cache_hit:
                catalog = cached[1]
            else:
                models = _fetch_models(key)
                catalog = {"models": models, "model_metadata": _fetch_metadata(key, models)}
                with self._lock:
                    if len(self._cache) >= 64:
                        self._cache.pop(next(iter(self._cache)))
                    self._cache[scope] = (time.monotonic(), catalog)
        return {"models": {kind: list(values) for kind, values in catalog["models"].items()},
                "model_metadata": {kind: {name: dict(info) for name, info in values.items()}
                                   for kind, values in catalog["model_metadata"].items()}, "account_id": scope[0],
                "site": scope[1], "source": "favorites", "cached": cache_hit}
