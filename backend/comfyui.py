"""HTTP execution and artifact retrieval for self-hosted ComfyUI servers."""

from __future__ import annotations

import re
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit

import httpx

from rh_cli.errors import RhCliError


PASSIVE_OUTPUT_EXTENSIONS = {
    "png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "avif",
    "mp4", "webm", "mov", "mkv", "avi",
    "wav", "mp3", "flac", "ogg", "opus", "m4a", "aac",
    "txt", "srt", "json", "glb", "obj", "stl",
}
TRANSIENT_HTTP_STATUSES = {502, 503, 504}


def normalize_comfyui_url(value: str) -> str:
    url = value.strip()
    if not url:
        return ""
    if "://" not in url:
        url = f"http://{url}"
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.port == 0
            or parsed.username is not None
            or parsed.password is not None
            or any(char in url for char in "?#\\")
            or any(char.isspace() or not char.isprintable() for char in url)
        ):
            raise ValueError
        return str(httpx.URL(url)).rstrip("/")
    except (ValueError, httpx.InvalidURL) as exc:
        raise RhCliError("INVALID_COMFYUI_URL", "请输入有效的 ComfyUI 主机、端口或 HTTP(S) 地址，认证信息请单独填写。") from exc


def _identifier(value: Any) -> str:
    """Only expose machine identifiers from remote errors, which can echo inputs."""
    return value if isinstance(value, str) and re.fullmatch(r"[\w.:-]{1,120}", value) else ""


class ComfyUIClient(httpx.Client):
    def __init__(self, url: str, token: str = "") -> None:
        super().__init__(
            base_url=url + "/",
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=httpx.Timeout(30, connect=5),
            trust_env=False,
            follow_redirects=False,
        )

    def _request_json(self, method: str, route: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.request(method, route, **kwargs)
            if route == "prompt" and response.status_code == 400:
                body = response.json()
                error = body.get("error") if isinstance(body, dict) else None
                errors = body.get("node_errors") if isinstance(body, dict) else None
                nodes = {}
                if isinstance(errors, dict):
                    for node_id, node in errors.items():
                        if not _identifier(node_id) or not isinstance(node, dict):
                            continue
                        reasons = node.get("errors")
                        nodes[node_id] = {
                            "class_type": _identifier(node.get("class_type")),
                            "errors": [
                                {
                                    "type": _identifier(reason.get("type")),
                                    "input_name": _identifier(reason["extra_info"].get("input_name"))
                                    if isinstance(reason.get("extra_info"), dict) else "",
                                }
                                for reason in (reasons if isinstance(reasons, list) else [])
                                if isinstance(reason, dict)
                            ],
                        }
                raise RhCliError(
                    "COMFYUI_NODE_ERRORS", "ComfyUI 拒绝了工作流，请检查节点、模型及输入字段。",
                    detail={"type": _identifier(error.get("type")) if isinstance(error, dict) else "", "node_errors": nodes},
                )
            response.raise_for_status()
            if not response.content:
                return {}
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except httpx.HTTPStatusError as exc:
            raise self._http_error(exc.response.status_code, route) from exc
        except httpx.RequestError as exc:
            raise RhCliError("COMFYUI_CONNECTION", "无法连接 ComfyUI 或请求超时。") from exc
        except ValueError as exc:
            raise RhCliError("COMFYUI_RESPONSE", "服务器返回的内容不符合 ComfyUI API 格式。") from exc

    @staticmethod
    def _http_error(status: int, route: str) -> RhCliError:
        message = "ComfyUI 认证失败，请检查服务器访问令牌。" if status in {401, 403} else f"ComfyUI 请求失败（HTTP {status}）。"
        return RhCliError("COMFYUI_HTTP", message, detail={"route": route, "status": status})

    def test_connection(self) -> None:
        stats = self._request_json("GET", "system_stats", timeout=8)
        if not isinstance(stats.get("system"), dict) or not isinstance(stats.get("devices"), list):
            raise RhCliError("COMFYUI_RESPONSE", "此地址没有返回 ComfyUI 系统信息，请检查地址和代理路径。")

    def upload(self, path: Path, task_id: str) -> str:
        with path.open("rb") as source:
            result = self._request_json(
                "POST", "upload/image", files={"image": (path.name, source)},
                data={"type": "input", "subfolder": f"rh-workflow-desk/{task_id}"},
            )
        name = result.get("name")
        folder = result.get("subfolder", "")
        if not isinstance(name, str) or not name or not isinstance(folder, str):
            raise RhCliError("COMFYUI_RESPONSE", "ComfyUI 未返回上传文件名。")
        return str(PurePosixPath(folder, name))

    def submit(self, workflow: dict[str, Any]) -> str:
        result = self._request_json("POST", "prompt", json={"prompt": workflow})
        prompt_id = _identifier(result.get("prompt_id"))
        if not prompt_id or prompt_id in {".", ".."}:
            raise RhCliError("COMFYUI_RESPONSE", "ComfyUI 未返回有效的任务 ID。")
        return prompt_id

    def check_cancel(self, cancel: threading.Event, prompt_id: str = "") -> None:
        if not cancel.is_set():
            return
        if not prompt_id:
            raise RhCliError("TASK_CANCELLED", "任务已取消。")
        try:
            # Queue deletion is scoped to this prompt on old and new servers.
            # Some versions ignore the ID on /interrupt and stop any active job.
            self._request_json("POST", "queue", json={"delete": [prompt_id]})
            running = self._queue_state(prompt_id) == "running"
        except RhCliError as exc:
            raise RhCliError("TASK_CANCELLED", "已停止等待，无法确认 ComfyUI 是否取消。") from exc
        message = "已停止等待，ComfyUI 上已开始的任务可能继续运行。" if running else "任务已取消。"
        raise RhCliError("TASK_CANCELLED", message)

    def _queue_state(self, prompt_id: str) -> str:
        queue = self._request_json("GET", "queue")
        for state in ("running", "pending"):
            rows = queue.get(f"queue_{state}")
            if not isinstance(rows, list):
                raise RhCliError("COMFYUI_RESPONSE", "ComfyUI 未返回有效的队列。")
            if any(isinstance(row, list) and len(row) >= 2 and row[1] == prompt_id for row in rows):
                return state
        return ""

    def wait_for_outputs(
        self,
        prompt_id: str,
        cancel: threading.Event,
        stop: threading.Event,
        on_state: Callable[[str], None],
        *,
        interval: float = 2,
    ) -> list[dict[str, Any]] | None:
        failures = missing = 0
        last_state = ""
        route = f"history/{quote(prompt_id, safe='')}"
        while not stop.is_set():
            self.check_cancel(cancel, prompt_id)
            try:
                entry = self._request_json("GET", route).get(prompt_id)
                if isinstance(entry, dict):
                    return self._history_outputs(entry)
                state = self._queue_state(prompt_id)
                failures = 0
            except RhCliError as exc:
                failures += 1
                status = exc.detail.get("status") if isinstance(exc.detail, dict) else None
                retryable = exc.code == "COMFYUI_CONNECTION" or status in TRANSIENT_HTTP_STATUSES
                if not retryable or failures >= 5:
                    raise
                state = "reconnecting"
            if state != last_state:
                on_state(state)
                last_state = state
            missing = 0 if state else missing + 1
            if missing >= 3:
                raise RhCliError("COMFYUI_TASK_MISSING", "ComfyUI 队列和历史记录中均找不到该任务。")
            cancel.wait(interval)
        return None

    def _history_outputs(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        status = entry.get("status")
        if not isinstance(status, dict):
            raise RhCliError("COMFYUI_RESPONSE", "ComfyUI 历史记录缺少任务状态。")
        if status.get("status_str") != "success":
            messages = status.get("messages")
            detail = {}
            for item in messages if isinstance(messages, list) else []:
                if isinstance(item, list) and len(item) == 2 and item[0] in {"execution_error", "execution_interrupted"} and isinstance(item[1], dict):
                    detail = {field: _identifier(item[1].get(field)) for field in ("node_id", "node_type", "exception_type")}
            raise RhCliError("COMFYUI_EXECUTION", "ComfyUI 执行失败或被服务器中断。", detail=detail)

        results = []
        seen = set()
        nodes = entry.get("outputs")
        for node_id, fields in nodes.items() if isinstance(nodes, dict) else []:
            if not isinstance(fields, dict):
                continue
            for key, value in fields.items():
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if key in {"text", "string"} and isinstance(item, str):
                        results.append({"nodeId": node_id, "text": item})
                        continue
                    if not isinstance(item, dict) or item.get("type") not in {"output", "temp"}:
                        continue
                    filename, subfolder = item.get("filename"), item.get("subfolder", "")
                    if not isinstance(filename, str) or not isinstance(subfolder, str):
                        continue
                    descriptor = (filename, subfolder, item["type"])
                    if descriptor in seen:
                        continue
                    seen.add(descriptor)
                    suffix = PurePosixPath(filename).suffix.lower().lstrip(".")
                    # The Desk serves local artifacts on its own origin. Use a
                    # passive type for unknown extensions, including HTML/SVG.
                    if suffix not in PASSIVE_OUTPUT_EXTENSIONS:
                        suffix = "bin"
                    args = urlencode(dict(zip(("filename", "subfolder", "type"), descriptor)))
                    results.append({"nodeId": node_id, "fileUrl": "view?" + args, "fileType": suffix})
        return results

    def download(self, route: str, path: str) -> None:
        try:
            with self.stream("GET", route) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").partition(";")[0].lower()
                if content_type == "text/html":
                    raise RhCliError("COMFYUI_DOWNLOAD", "ComfyUI 产物接口返回了网页，请检查服务器或反向代理。")
                with Path(path).open("wb") as target:
                    for chunk in response.iter_bytes():
                        target.write(chunk)
        except httpx.HTTPStatusError as exc:
            raise self._http_error(exc.response.status_code, "view") from exc
        except httpx.RequestError as exc:
            raise RhCliError("COMFYUI_DOWNLOAD", "下载 ComfyUI 产物时连接中断。") from exc
