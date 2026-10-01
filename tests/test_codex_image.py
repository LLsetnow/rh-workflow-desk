import json
import subprocess
from pathlib import Path

import pytest

from backend import toolbox
from rh_cli.errors import RhCliError


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.delenv("RH_CODEX_AGENT_MODEL", raising=False)
    monkeypatch.delenv("RH_CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(toolbox.shutil, "which", lambda name: "/fake/codex")
    return {
        "prompt": "一只猫 && echo '$HOME' `touch bad` {output}",
        "output": str(tmp_path / "result.png"),
        "model": "gpt-image-2.5-sunburst",
        "resolution": "2k",
        "size": "16:9",
        "references": [],
    }


def write_response(command, value):
    Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("reference_count", [0, 2])
def test_direct_codex_exec_preserves_prompt_references_and_canvas(context, tmp_path, monkeypatch, reference_count):
    references = [tmp_path / f"参考图 {index}.png" for index in range(reference_count)]
    for reference in references:
        reference.write_bytes(b"original")
    context["references"] = [str(path) for path in references]
    # A legacy override must never route the new runner through OPC.
    monkeypatch.setenv("RH_CODEX_IMAGE_COMMAND", "opc image generate {prompt} --output {output}")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert command[:2] == ["/fake/codex", "exec"]
        assert "--ignore-user-config" in command
        assert command[command.index("--sandbox") + 1] == "workspace-write"
        assert command[command.index("-c") + 1] == 'approval_policy="never"'
        assert "--model" not in command
        assert command[command.index("-C") + 1] == str(tmp_path)
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs.get("shell", False) is False
        prompt_index = next(index for index, value in enumerate(command) if context["prompt"] in value)
        prompt = command[prompt_index]
        assert context["model"] in prompt
        assert context["resolution"] in prompt
        assert context["size"] in prompt
        assert context["output"] in prompt
        assert command[prompt_index + 1:] == [value for path in references for value in ("--image", str(path))]
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        assert schema["required"] == ["image_path"]
        assert schema["additionalProperties"] is False
        Path(context["output"]).write_bytes(b"generated-image")
        write_response(command, {"image_path": context["output"]})
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr(toolbox.subprocess, "run", fake_run)
    assert toolbox.run_codex_image(context, cwd=tmp_path) == [Path(context["output"])]
    assert len(calls) == 1
    assert all(reference.read_bytes() == b"original" for reference in references)
    assert not list(tmp_path.glob(".codex-image-*"))


def test_explicit_agent_model_is_separate_from_image_model(context, tmp_path, monkeypatch):
    monkeypatch.setenv("RH_CODEX_AGENT_MODEL", "gpt-6-sol")
    monkeypatch.setenv("RH_CODEX_CLI_PATH", "/custom/codex")
    looked_up = []

    def which(candidate):
        looked_up.append(candidate)
        return candidate

    def fake_run(command, **kwargs):
        assert command[0] == "/custom/codex"
        assert command[command.index("--model") + 1] == "gpt-6-sol"
        assert context["model"] in command[-1]
        Path(context["output"]).write_bytes(b"image")
        write_response(command, {"image_path": context["output"]})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(toolbox.shutil, "which", which)
    monkeypatch.setattr(toolbox.subprocess, "run", fake_run)
    toolbox.run_codex_image(context, cwd=tmp_path)
    assert looked_up == ["/custom/codex"]


@pytest.mark.parametrize("failure", ["missing_json", "invalid_json", "missing_path", "different_path", "relative_path", "missing_file", "empty_file"])
def test_successful_exit_requires_this_tasks_final_image(context, tmp_path, monkeypatch, failure):
    # A reference or stray media file cannot masquerade as the generated result.
    unrelated = tmp_path / "reference.png"
    unrelated.write_bytes(b"original")

    def fake_run(command, **kwargs):
        if failure == "invalid_json":
            Path(command[command.index("--output-last-message") + 1]).write_text("not json")
        elif failure != "missing_json":
            value = {"image_path": context["output"]}
            if failure == "missing_path":
                value = {}
            elif failure == "different_path":
                value["image_path"] = str(unrelated)
            elif failure == "relative_path":
                value["image_path"] = "result.png"
            write_response(command, value)
        if failure != "missing_file":
            Path(context["output"]).write_bytes(b"" if failure == "empty_file" else b"image")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(toolbox.subprocess, "run", fake_run)
    with pytest.raises(RhCliError) as caught:
        toolbox.run_codex_image(context, cwd=tmp_path)
    assert caught.value.code in {"TOOLBOX_OUTPUT_MISSING", "TOOLBOX_OUTPUT_INVALID"}
    assert unrelated.read_bytes() == b"original"
    assert not list(tmp_path.glob(".codex-image-*"))


def test_codex_cli_not_found_does_not_try_opc(context, tmp_path, monkeypatch):
    monkeypatch.setattr(toolbox.shutil, "which", lambda name: None)
    with pytest.raises(RhCliError) as caught:
        toolbox.run_codex_image(context, cwd=tmp_path)
    assert caught.value.code == "TOOLBOX_COMMAND_NOT_FOUND"


def test_codex_timeout_is_reported_and_cleans_up(context, tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(toolbox.subprocess, "run", fake_run)
    with pytest.raises(RhCliError) as caught:
        toolbox.run_codex_image(context, cwd=tmp_path, timeout=1)
    assert caught.value.code == "TOOLBOX_COMMAND_TIMEOUT"
    assert not list(tmp_path.glob(".codex-image-*"))
