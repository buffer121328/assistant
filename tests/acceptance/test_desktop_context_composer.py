from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "frontend" / "desktop" / "src"


def _read(relative_path: str) -> str:
    """Read one desktop source file for a focused compiled-contract assertion."""
    return (DESKTOP / relative_path).read_text(encoding="utf-8")


def test_native_context_file_bridge_is_explicit_bounded_and_isolated() -> None:
    """Picker/drop paths cross only narrow Electron APIs with a 32-file cap."""
    main = _read("main/index.ts")
    preload = _read("preload/index.ts")

    assert "dialog" in main
    assert "MAX_CONTEXT_FILES = 32" in main
    assert 'ipcMain.handle("context:choose-files"' in main
    assert 'properties: ["openFile", "multiSelections"]' in main
    assert "new Set(result.filePaths)" in main
    assert ".slice(0, MAX_CONTEXT_FILES)" in main

    assert 'from "electron"' in preload
    assert "webUtils" in preload
    assert "chooseContextFiles" in preload
    assert 'ipcRenderer.invoke("context:choose-files")' in preload
    assert "pathForDroppedFile" in preload
    assert "webUtils.getPathForFile(file)" in preload
    assert "readFile" not in preload
    assert "readdir" not in preload


def test_typed_client_uses_owner_scoped_path_free_resource_contracts() -> None:
    """Renderer calls existing governed routes and sends stable IDs on continuation."""
    api = _read("renderer/api.ts")

    resource_type = api[api.index("export type ResourceReference"):]
    resource_type = resource_type[: resource_type.index("};")]
    assert "reference_id: string" in resource_type
    assert "display_name: string" in resource_type
    assert "size_bytes: number" in resource_type
    assert "source_ref" not in resource_type
    assert "source_path" not in resource_type

    assert "listConversationResources" in api
    assert "attachConversationResource" in api
    assert "uploadConversationResource" in api
    assert "attachArtifactResource" in api
    assert "attachExternalRecordResource" in api
    assert "/resources/upload" in api
    assert "/resources/artifact" in api
    assert "/resources/external-record" in api
    assert "removeConversationResource" in api
    assert "searchConversationMentions" in api
    assert "/local/conversations/${encodeURIComponent(conversationId)}/resources" in api
    assert "`/local/mentions?${params.toString()}`" in api
    assert "resourceReferenceIds: string[] = []" in api
    assert "resource_reference_ids: resourceReferenceIds" in api


def test_active_thread_context_ui_resets_selects_mentions_and_preserves_drafts() -> None:
    """Composer state follows Conversation identity and stable resource selections."""
    app = _read("renderer/App.tsx")
    styles = _read("renderer/styles.css")

    assert "conversationResources" in app
    assert "selectedResourceIds" in app
    assert "contextPending" in app
    assert "contextError" in app
    assert "mentionSuggestions" in app
    assert "mentionQuery" in app
    assert "api.listConversationResources" in app
    assert "api.searchConversationMentions" in app
    assert "window.assistantDesktop.chooseContextFiles()" in app
    assert "window.assistantDesktop.pathForDroppedFile(file)" in app
    assert "api.attachConversationResource" in app
    assert "api.uploadConversationResource" in app
    assert "api.attachArtifactResource" in app
    assert "api.attachExternalRecordResource" in app
    assert "Upload file" in app
    assert "Attach Artifact" in app
    assert "Attach external record identity" in app
    assert "api.removeConversationResource" in app
    assert "resource.reference_id" in app
    assert 'aria-label="Add context"' in app
    assert 'onDrop={(event) => void handleContextDrop(event)}' in app
    assert "No context attached" in app
    assert "Loading context" in app
    assert "Context unavailable" in app

    submit_index = app.index(
        "api.appendMessage(selectedTask.task_id, messageText.trim(), selectedResourceIds)"
    )
    clear_index = app.index("setSelectedResourceIds([])", submit_index)
    assert clear_index > submit_index

    assert ".context-panel" in styles
    assert ".context-resource" in styles
    assert ".mention-suggestions" in styles
    assert ".composer.drag-active" in styles
    assert ".context-error" in styles
    assert ".context-actions" in styles


def test_context_composer_keeps_initial_task_attachment_out_of_scope() -> None:
    """The new-task API remains text-only until a persisted Conversation exists."""
    api = _read("renderer/api.ts")
    create_task = api[api.index("async createTask"): api.index("async appendMessage")]

    assert "resource_reference_ids" not in create_task
    assert "local_file_paths" not in create_task
