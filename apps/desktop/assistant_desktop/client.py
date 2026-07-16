from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Any, Literal
from urllib.parse import urlparse

import httpx


JsonObject = dict[str, Any]
ApprovalDecision = Literal["approved", "rejected"]
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
MAX_SKILL_PACKAGE_BYTES = 1024 * 1024
MAX_KNOWLEDGE_DOCUMENT_BYTES = 20 * 1024 * 1024
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class DesktopApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class SubmissionResult:
    task: JsonObject
    queued: bool


@dataclass(frozen=True)
class ApprovalDecisionResult:
    approval: JsonObject
    task: JsonObject
    queued: bool


def normalize_connection_settings(base_url: str, user_id: str) -> tuple[str, str]:
    normalized_url = base_url.strip().rstrip("/")
    parsed = urlparse(normalized_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DesktopApiError("API 地址必须是有效的 HTTP 或 HTTPS 地址。")
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise DesktopApiError("请先填写已有用户 ID。")
    return normalized_url, normalized_user_id


class DesktopApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        user_id: str,
        api_token: str = "",
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url, self.user_id = normalize_connection_settings(base_url, user_id)
        headers = (
            {"authorization": f"Bearer {api_token.strip()}"}
            if api_token.strip()
            else None
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            transport=transport,
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def submit_task(
        self,
        *,
        task_type: str,
        input_text: str,
        conversation_id: str | None = None,
    ) -> SubmissionResult:
        payload = self._request(
            "POST",
            "/api/tasks/submit",
            json={
                "user_id": self.user_id,
                "platform": "desktop",
                "task_type": task_type,
                "input_text": input_text,
                "conversation_id": conversation_id,
            },
        )
        return SubmissionResult(
            task=self._object(payload, "task"),
            queued=bool(payload.get("queued")),
        )

    def stream_task_events(self, task_id: str, *, after: int = 0):
        try:
            with self._client.stream(
                "GET",
                f"/api/tasks/{task_id}/events/stream",
                params={"user_id": self.user_id, "after": after},
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    payload = json.loads(line)
                    if not isinstance(payload, dict):
                        raise DesktopApiError("任务事件格式无效。")
                    yield payload
        except httpx.HTTPStatusError as exc:
            raise self._status_error(exc.response) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise DesktopApiError("任务事件流已断开。") from exc

    def create_conversation(self, title: str | None = None) -> JsonObject:
        payload: JsonObject = {"user_id": self.user_id}
        if title:
            payload["title"] = title
        return self._request("POST", "/api/conversations", json=payload)

    def list_conversations(self) -> list[JsonObject]:
        payload = self._request(
            "GET", "/api/conversations", params={"user_id": self.user_id}
        )
        return self._object_list(payload, "items")

    def get_conversation_messages(self, conversation_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/api/conversations/{conversation_id}/messages",
            params={"user_id": self.user_id},
        )

    def list_conversation_messages(self, conversation_id: str) -> list[JsonObject]:
        return self._object_list(
            self.get_conversation_messages(conversation_id), "items"
        )

    def archive_conversation(self, conversation_id: str) -> JsonObject:
        return self._request(
            "POST",
            f"/api/conversations/{conversation_id}/archive",
            json={"user_id": self.user_id},
        )

    def list_tasks(self) -> list[JsonObject]:
        payload = self._request(
            "GET",
            "/api/tasks",
            params={"user_id": self.user_id},
        )
        return self._object_list(payload, "items")

    def get_task_memory_retrieval(self, task_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/api/tasks/{task_id}/memory-retrieval",
            params={"user_id": self.user_id},
        )

    def get_memory_overview(self) -> JsonObject:
        return self._request(
            "GET",
            "/api/memories/overview",
            params={"user_id": self.user_id},
        )

    def create_memory(
        self,
        *,
        content: str,
        memory_type: str = "preference",
        scope_kind: str = "user/global",
        scope_id: str | None = None,
    ) -> JsonObject:
        return self._request(
            "POST",
            "/api/memories",
            json={
                "user_id": self.user_id,
                "content": content,
                "memory_type": memory_type,
                "scope_kind": scope_kind,
                "scope_id": scope_id,
            },
        )

    def list_memories(
        self,
        *,
        status: str | None = None,
        memory_type: str | None = None,
        scope_kind: str | None = None,
        sensitivity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JsonObject:
        params: dict[str, object] = {
            "user_id": self.user_id,
            "limit": limit,
            "offset": offset,
        }
        for key, value in (
            ("status", status),
            ("memory_type", memory_type),
            ("scope_kind", scope_kind),
            ("sensitivity", sensitivity),
        ):
            if value:
                params[key] = value
        return self._request("GET", "/api/memories", params=params)

    def get_memory_detail(self, memory_id: str) -> JsonObject:
        return self._request(
            "GET",
            f"/api/memories/{memory_id}",
            params={"user_id": self.user_id},
        )

    def perform_memory_action(
        self, memory_id: str, action: str, **values: object
    ) -> JsonObject:
        return self._request(
            "POST",
            f"/api/memories/{memory_id}/actions/{action}",
            json={"user_id": self.user_id, **values},
        )

    def list_memory_policies(self) -> list[JsonObject]:
        payload = self._request(
            "GET", "/api/memory/policies", params={"user_id": self.user_id}
        )
        return self._object_list(payload, "items")

    def update_memory_policy(
        self,
        policy_key: str,
        *,
        enabled: bool,
        scope_kind: str = "user/global",
        scope_id: str | None = None,
    ) -> JsonObject:
        return self._request(
            "PUT",
            f"/api/memory/policies/{policy_key}",
            json={
                "user_id": self.user_id,
                "enabled": enabled,
                "scope_kind": scope_kind,
                "scope_id": scope_id,
            },
        )

    def list_memory_digests(self, *, limit: int = 20) -> list[JsonObject]:
        payload = self._request(
            "GET",
            "/api/memory/consolidation-digests",
            params={"user_id": self.user_id, "limit": limit},
        )
        return self._object_list(payload, "items")

    def list_approvals(self, task_id: str) -> list[JsonObject]:
        payload = self._request(
            "GET",
            f"/api/tasks/{task_id}/approvals",
            params={"user_id": self.user_id},
        )
        return self._object_list(payload, "items")

    def decide_approval(
        self,
        task_id: str,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalDecisionResult:
        payload = self._request(
            "POST",
            f"/api/tasks/{task_id}/approvals/{approval_id}/decision",
            json={"user_id": self.user_id, "decision": decision},
        )
        return ApprovalDecisionResult(
            approval=self._object(payload, "approval"),
            task=self._object(payload, "task"),
            queued=bool(payload.get("queued")),
        )

    def list_skills(self) -> list[JsonObject]:
        payload = self._request("GET", "/api/skills")
        return self._object_list(payload, "items")

    def list_connections(self) -> list[JsonObject]:
        payload = self._request(
            "GET",
            "/api/connections",
            params={"user_id": self.user_id},
        )
        return self._object_list(payload, "items")

    def create_connection(
        self,
        *,
        provider: str,
        display_name: str,
        credentials: dict[str, str],
    ) -> JsonObject:
        return self._request(
            "POST",
            "/api/connections",
            json={
                "user_id": self.user_id,
                "provider": provider,
                "display_name": display_name,
                "credentials": credentials,
            },
        )

    def test_connection(self, connection_id: str) -> JsonObject:
        return self._request(
            "POST",
            f"/api/connections/{connection_id}/test",
            json={"user_id": self.user_id},
        )

    def disable_connection(self, connection_id: str) -> JsonObject:
        return self._request(
            "POST",
            f"/api/connections/{connection_id}/disable",
            json={"user_id": self.user_id},
        )

    def revoke_connection(self, connection_id: str) -> JsonObject:
        return self._request(
            "DELETE",
            f"/api/connections/{connection_id}",
            params={"user_id": self.user_id},
        )

    def import_knowledge(self, document_path: Path) -> JsonObject:
        try:
            size = document_path.stat().st_size
        except OSError as exc:
            raise DesktopApiError("无法读取知识库文件。") from exc
        if not 0 < size <= MAX_KNOWLEDGE_DOCUMENT_BYTES:
            raise DesktopApiError("知识库文件必须小于等于 20 MiB。")
        try:
            with document_path.open("rb") as document:
                return self._request(
                    "POST",
                    "/api/knowledge/import",
                    data={"user_id": self.user_id},
                    files={
                        "document": (
                            document_path.name,
                            document,
                            "application/octet-stream",
                        )
                    },
                )
        except OSError as exc:
            raise DesktopApiError("无法读取知识库文件。") from exc

    def list_knowledge_documents(self) -> list[JsonObject]:
        payload = self._request(
            "GET",
            "/api/knowledge/documents",
            params={"user_id": self.user_id},
        )
        return self._object_list(payload, "items")

    def search_knowledge(self, query: str, *, limit: int = 5) -> list[JsonObject]:
        payload = self._request(
            "GET",
            "/api/knowledge/search",
            params={"user_id": self.user_id, "query": query, "limit": limit},
        )
        return self._object_list(payload, "items")

    def create_reminder(
        self,
        *,
        title: str,
        message: str,
        due_at: str,
        channel: str,
    ) -> JsonObject:
        return self._request(
            "POST",
            "/api/reminders",
            json={
                "user_id": self.user_id,
                "title": title,
                "message": message,
                "due_at": due_at,
                "channel": channel,
            },
        )

    def list_reminders(self) -> list[JsonObject]:
        payload = self._request(
            "GET", "/api/reminders", params={"user_id": self.user_id}
        )
        return self._object_list(payload, "items")

    def cancel_reminder(self, reminder_id: str) -> JsonObject:
        return self._request(
            "POST",
            f"/api/reminders/{reminder_id}/cancel",
            json={"user_id": self.user_id},
        )

    def poll_notifications(self) -> list[JsonObject]:
        payload = self._request(
            "GET", "/api/notifications/poll", params={"user_id": self.user_id}
        )
        return self._object_list(payload, "items")

    def acknowledge_notification(self, outbox_id: str) -> None:
        self._request_no_content(
            "POST",
            f"/api/notifications/{outbox_id}/ack",
            json={"user_id": self.user_id},
        )

    def create_skill(
        self,
        *,
        name: str,
        display_name: str,
        summary: str,
        instructions: str,
    ) -> JsonObject:
        skill_name = self._skill_name(name)
        return self._request(
            "POST",
            "/api/skills",
            json={
                "user_id": self.user_id,
                "name": skill_name,
                "display_name": display_name,
                "summary": summary,
                "instructions": instructions,
            },
        )

    def install_skill(self, package_path: Path) -> JsonObject:
        try:
            size = package_path.stat().st_size
        except OSError as exc:
            raise DesktopApiError("无法读取 Skill 安装包。") from exc
        if size > MAX_SKILL_PACKAGE_BYTES:
            raise DesktopApiError("Skill 安装包不能超过 1 MiB。")
        try:
            with package_path.open("rb") as package:
                return self._request(
                    "POST",
                    "/api/skills/install",
                    data={"user_id": self.user_id},
                    files={
                        "package": (
                            package_path.name,
                            package,
                            "application/zip",
                        )
                    },
                )
        except OSError as exc:
            raise DesktopApiError("无法读取 Skill 安装包。") from exc

    def set_skill_enabled(self, name: str, *, enabled: bool) -> JsonObject:
        skill_name = self._skill_name(name)
        action = "enable" if enabled else "disable"
        return self._request(
            "POST",
            f"/api/skills/{skill_name}/{action}",
            json={"user_id": self.user_id},
        )

    def uninstall_skill(self, name: str) -> None:
        skill_name = self._skill_name(name)
        self._request_no_content(
            "DELETE",
            f"/api/skills/{skill_name}",
            params={"user_id": self.user_id},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> JsonObject:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise self._status_error(exc.response) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise DesktopApiError("无法连接 API 服务或响应格式无效。") from exc
        if not isinstance(payload, dict):
            raise DesktopApiError("API 响应格式无效。")
        return payload

    def _request_no_content(self, method: str, path: str, **kwargs: Any) -> None:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._status_error(exc.response) from exc
        except httpx.HTTPError as exc:
            raise DesktopApiError("无法连接 API 服务。") from exc

    @staticmethod
    def _status_error(response: httpx.Response) -> DesktopApiError:
        code = ""
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                code = f"{error['code']}，"
        return DesktopApiError(f"服务请求失败（{code}状态码 {response.status_code}）。")

    @staticmethod
    def _skill_name(name: str) -> str:
        normalized = name.strip()
        if not SKILL_NAME_PATTERN.fullmatch(normalized):
            raise DesktopApiError("Skill 名称只能使用小写字母、数字和单连字符。")
        return normalized

    @staticmethod
    def _object(payload: JsonObject, key: str) -> JsonObject:
        value = payload.get(key)
        if not isinstance(value, dict):
            raise DesktopApiError("API 响应缺少必要对象。")
        return value

    @staticmethod
    def _object_list(payload: JsonObject, key: str) -> list[JsonObject]:
        value = payload.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value
        ):
            raise DesktopApiError("API 响应缺少必要列表。")
        return value
