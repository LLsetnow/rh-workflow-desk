import json

import pytest

import backend.app as web_app
from tests.test_app import _configure_web_paths


def test_library_save_creates_complete_three_file_package(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        workflow_id, workflow_path, _ = store.save_workflow(
            "demo.json",
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}),
        )
        prompt_group_path = web_app.WORKFLOW_ROOT / f"{workflow_id}.prompt_group.json"
        registration_path = store._workflow_registry_entry_path(workflow_id)
        assert workflow_path.is_file()
        assert prompt_group_path.is_file()
        assert registration_path.is_file()
        registration = json.loads(registration_path.read_text(encoding="utf-8"))
        assert registration["workflow_file"] == f"workflows/{workflow_id}.json"
        assert registration["prompt_group_file"] == f"workflows/{workflow_id}.prompt_group.json"
        assert json.loads(prompt_group_path.read_text(encoding="utf-8"))["items"] == []
    finally:
        store._db.close()


def test_library_save_prunes_manual_config_for_bypassed_node(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        workflow = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "保留"}},
        }
        input_config = {
            "mode": "manual",
            "items": [
                {"id": "97:steps", "node_id": "97", "field": "steps", "kind": "number"},
                {"id": "1:text", "node_id": "1", "field": "text", "kind": "text"},
            ],
        }
        workflow_id, _, _ = store.save_workflow(
            "bypassed.json", json.dumps(workflow), input_config=input_config,
        )
        saved_items = store.workflow_record(workflow_id)["input_config"]["items"]
        assert [item["id"] for item in saved_items] == ["1:text"]
    finally:
        store._db.close()


def test_replacement_migrates_active_telegram_reference_before_retiring_old_package(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        account = store.add_account("账号", "cn")
        workflow = {
            "1": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        old_id, _, _ = store.save_workflow(
            "old.json", json.dumps(workflow), account_id=account["id"], remote_workflow_id="1"
        )
        store.set_telegram_settings("token", "1", False)
        store.set_telegram_inbound_settings(old_id, False)
        result = store.replace_workflow(
            old_id,
            "new.json",
            json.dumps(workflow),
            account_id=account["id"],
            remote_workflow_id="2",
        )
        new_id = result["record"]["id"]
        assert new_id != old_id
        assert store._read_json_file()["telegram_inbound_workflow_id"] == new_id
        assert store.workflow_record(new_id)["name"] == "new.json"
        with pytest.raises(web_app.RhCliError) as excinfo:
            store.workflow_record(old_id)
        assert excinfo.value.code == "WORKFLOW_NOT_FOUND"
    finally:
        store._db.close()


def test_direct_delete_rejects_active_reference(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    try:
        workflow_id, _, _ = store.save_workflow(
            "active.json",
            json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}}}),
        )
        store.set_telegram_settings("token", "1", False)
        store.set_telegram_inbound_settings(workflow_id, False)
        with pytest.raises(web_app.RhCliError) as excinfo:
            store.delete_workflow(workflow_id)
        assert excinfo.value.code == "WORKFLOW_REFERENCED"
        assert store.workflow_record(workflow_id)["id"] == workflow_id
    finally:
        store._db.close()


def test_task_snapshot_survives_library_replacement(tmp_path, monkeypatch):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    try:
        workflow = {"1": {"class_type": "SaveImage", "inputs": {}}}
        old_id, _, _ = store.save_workflow("old.json", json.dumps(workflow), remote_workflow_id="1")
        task = manager.submit_task(
            old_id,
            {},
            {},
            None,
            None,
            remote_workflow_id="1",
            workflow_data=workflow,
            workflow_name="old.json",
        )
        store.replace_workflow(old_id, "new.json", json.dumps(workflow), remote_workflow_id="2")
        loaded = store.load_task_workflow(task["id"])
        assert loaded["workflow"] == workflow
        assert loaded["task"]["registered_workflow_id"] == old_id
        assert loaded["workflow_path"].endswith("workflow_api.json")
    finally:
        manager.close()
        store._db.close()


@pytest.mark.parametrize("media_edit", ["added", "removed"])
def test_qwen_history_resubmission_preserves_edited_snapshot(tmp_path, monkeypatch, media_edit):
    _configure_web_paths(tmp_path, monkeypatch)
    store = web_app.LocalStore()
    manager = web_app.TaskManager(store)
    image = tmp_path / "reference.png"
    image.write_bytes(b"local-reference")
    library_workflow = {
        "485": {"class_type": "TextEncodeQwenImage21", "inputs": {"prompt": "original"}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0, "mode": "fixed"}},
    }
    if media_edit == "removed":
        library_workflow["470"] = {"class_type": "LoadImage", "inputs": {"image": "original.png"}}
        library_workflow["485"]["inputs"]["images.image_1"] = ["470", 0]
    snapshot = {
        "485": {"class_type": "TextEncodeQwenImage21", "inputs": {"prompt": "saved prompt"}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42, "mode": "fixed"}},
    }
    files = {}
    config = {"mode": "manual", "items": [{"id": "485:prompt", "kind": "prompt"}]}
    if media_edit == "added":
        snapshot["523"] = {"class_type": "LoadImage", "inputs": {"image": str(image)}}
        snapshot["485"]["inputs"]["images.image_1"] = ["523", 0]
        files["523:image"] = str(image)
        config["items"].append({"id": "523:image", "kind": "file", "required": True})
    try:
        workflow_id, library_path, _ = store.save_workflow(
            "qwen.json", json.dumps(library_workflow), remote_workflow_id="123456",
        )
        library_before = library_path.read_bytes()
        original = manager.submit_task(
            workflow_id, files, {"485:prompt": "saved prompt"}, None, None,
            remote_workflow_id="123456", workflow_data=snapshot,
            workflow_input_config=config, random_noise={"7": {"seed": 42, "mode": "fixed"}},
        )
        original_path = store.task_snapshot_path(original)
        snapshot_before = original_path.read_bytes()
        loaded = store.load_task_replay(original["id"])
        saved_task = loaded["task"]
        replay = manager.submit_task(
            loaded["workflow_id"], saved_task["files"], saved_task["prompts"], None, None,
            remote_workflow_id=saved_task["remote_workflow_id"],
            workflow_data=loaded["workflow"], workflow_input_config=loaded["input_config"],
            random_noise=saved_task["random_noise"], resolution=saved_task["resolution"],
            custom_inputs=saved_task["custom_inputs"], bypassed_nodes=saved_task["bypassed_nodes"],
        )
        replay_snapshot = json.loads(store.task_snapshot_path(replay).read_text(encoding="utf-8"))
        assert replay_snapshot == loaded["workflow"]
        assert replay["files"] == files
        assert replay["input_config"] == loaded["input_config"]
        assert replay["random_noise"] == saved_task["random_noise"]
        assert replay["registered_workflow_id"] == workflow_id
        assert library_path.read_bytes() == library_before
        assert original_path.read_bytes() == snapshot_before
    finally:
        manager.close()
        store._db.close()
