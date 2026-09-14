from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from collections.abc import Mapping
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.policies.redaction import sanitize_text
from domain.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    EvolutionChange,
    EvolutionVersion,
    Task,
    TaskStatus,
    utc_now,
)

from .types import (
    FILE_TO_MODULE,
    MODULE_FILES,
    PromptBuildResult,
    PromptModule,
    PromptModuleName,
)

_POLICY_DOWNGRADE = re.compile(
    r"(?i)(disable|关闭|绕过|bypass).{0,30}(approval|审批|toolregistry|tool registry|risk|风险|permission|权限)"
)
_SECRET_LIKE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd)\s*[:=]\s*\S+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{8,}"
)


class PromptSource(Protocol):
    """定义当前组件的接口契约。"""

    def load_module(
        self,
        *,
        module_name: PromptModuleName,
        filename: str,
        runtime_context: Mapping[str, Any] | None = None,
    ) -> PromptModule | None:
        """Load a prompt module or return None to use local fallback.

        Args:
            module_name: 用于执行当前操作的 module name 参数。
            filename: 用于执行当前操作的 filename 参数。
            runtime_context: 用于执行当前操作的 runtime context 参数。
        """
        ...


class PromptValidationError(ValueError):
    """定义当前组件可安全处理的错误类型。"""

    pass


class PromptStore:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        *,
        defaults_root: Path,
        managed_root: Path,
        max_module_bytes: int = 64_000,
        prompt_source: PromptSource | None = None,
    ) -> None:
        """Initialize prompt roots and validation limits.

        Args:
            defaults_root: 用于执行当前操作的 defaults root 参数。
            managed_root: 用于执行当前操作的 managed root 参数。
            max_module_bytes: 用于执行当前操作的 max module bytes 参数。
            prompt_source: 用于执行当前操作的 prompt source 参数。
        """
        self.defaults_root = defaults_root.expanduser().resolve()
        self.managed_root = managed_root.expanduser().resolve()
        self.max_module_bytes = max_module_bytes
        self.prompt_source = prompt_source
        self._prompt_source_failures: dict[str, str] = {}

    def build(self, runtime_context: dict[str, Any] | None = None) -> PromptBuildResult:
        """Build a system prompt from all configured modules.

        Args:
            runtime_context: 用于执行当前操作的 runtime context 参数。
        """
        self._prompt_source_failures = {}
        modules = tuple(
            self.load_module(name, runtime_context=runtime_context or {})
            for name in MODULE_FILES
        )
        context = sanitize_text(runtime_context or {})
        parts = [
            f"<!-- module:{module.name} source:{module.source} fingerprint:{module.fingerprint[:12]} -->\n{module.content.strip()}"
            for module in modules
        ]
        parts.append(f"<!-- runtime_context -->\n{context[:12_000]}")
        prompt = "\n\n".join(parts).strip() + "\n"
        fingerprint = _checksum(prompt)
        return PromptBuildResult(
            system_prompt=prompt,
            modules=modules,
            fingerprint=fingerprint,
            metadata={
                "prompt_fingerprint": fingerprint,
                "modules": [
                    {
                        "name": module.name,
                        "source": module.source,
                        "fingerprint": module.fingerprint,
                        "version": module.version,
                        **module.metadata,
                    }
                    for module in modules
                ],
                "prompt_source_failures": dict(self._prompt_source_failures),
            },
        )

    def load_module(
        self, name: str, *, runtime_context: Mapping[str, Any] | None = None
    ) -> PromptModule:
        """Load one prompt module, preferring optional source then managed/defaults.

        Args:
            name: 目标对象或能力的名称。
            runtime_context: 用于执行当前操作的 runtime context 参数。
        """
        module_name = self.validate_module_name(name)
        filename = MODULE_FILES[module_name]
        if self.prompt_source is not None:
            try:
                sourced = self.prompt_source.load_module(
                    module_name=module_name,
                    filename=filename,
                    runtime_context=runtime_context,
                )
                if sourced is not None:
                    self.validate_content(sourced.content)
                    return sourced
            except Exception as exc:
                self._prompt_source_failures[filename] = sanitize_text(
                    exc,
                    extra_sensitive_values=getattr(
                        self.prompt_source, "sensitive_values", ()
                    ),
                )[:300]
        managed = self.managed_root / filename
        if _safe_existing_file(managed, self.managed_root, self.max_module_bytes):
            content = managed.read_text(encoding="utf-8")
            return PromptModule(
                module_name,
                filename,
                content,
                "managed",
                _checksum(content),
                self._latest_version(filename),
            )
        default = self.defaults_root / filename
        if not _safe_existing_file(default, self.defaults_root, self.max_module_bytes):
            raise PromptValidationError(
                f"Default prompt module is unavailable: {module_name}"
            )
        content = default.read_text(encoding="utf-8")
        return PromptModule(
            module_name, filename, content, "default", _checksum(content), None
        )

    def inspect(self) -> dict[str, object]:
        """执行当前组件定义的业务处理逻辑。"""
        modules = [self.load_module(name) for name in MODULE_FILES]
        return {
            "modules": [
                {
                    "name": item.name,
                    "source": item.source,
                    "fingerprint": item.fingerprint,
                    "summary": item.content.strip()[:160],
                    "version": item.version,
                }
                for item in modules
            ]
        }

    async def propose_change(
        self,
        *,
        session: AsyncSession,
        task_id: str,
        user_id: str,
        module_name: str,
        content: str,
        evidence: str,
    ) -> EvolutionChange:
        """Create a governed prompt-change proposal and approval request.

        Args:
            session: 当前数据库异步会话。
            task_id: 目标任务 ID。
            user_id: 目标用户 ID。
            module_name: 用于执行当前操作的 module name 参数。
            content: 用于执行当前操作的 content 参数。
            evidence: 用于执行当前操作的 evidence 参数。
        """
        module = self.validate_module_name(module_name)
        candidate = self.validate_content(content)
        safe_evidence = sanitize_text(evidence)[:2_000]
        if not safe_evidence.strip():
            raise PromptValidationError("Prompt change evidence is required")
        task = await session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        if task is None:
            raise PromptValidationError("Prompt change task is unavailable")
        filename = MODULE_FILES[module]
        current = self._read_managed(filename)
        change = EvolutionChange(
            task_id=task_id,
            user_id=user_id,
            target_kind="prompt",
            target_name=filename,
            base_checksum=_checksum(current),
            candidate_checksum=_checksum(candidate),
            candidate_content=candidate,
            evidence=safe_evidence,
            validation_result=json.dumps(
                {
                    "module": module,
                    "path_safe": True,
                    "size_safe": True,
                    "policy_safe": True,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            status="pending",
        )
        session.add(change)
        await session.flush()
        session.add(
            Approval(
                task_id=task_id,
                status=ApprovalStatus.PENDING.value,
                tool_name="prompt.propose_change",
                approval_type=ApprovalType.CHANGE.value,
                subject=change.id,
                request_summary=f"Prompt module change: {module}",
            )
        )
        if task.status in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}:
            task.status = TaskStatus.WAITING_APPROVAL.value
            task.result_text = "Prompt change proposal is waiting for approval."
            task.error_message = None
        await session.flush()
        return change

    async def apply_change(
        self, *, session: AsyncSession, change_id: str, user_id: str
    ) -> EvolutionChange:
        """Apply an approved managed prompt change.

        Args:
            session: 当前数据库异步会话。
            change_id: 用于执行当前操作的 change id 参数。
            user_id: 目标用户 ID。
        """
        change = await self._owned_change(session, change_id, user_id)
        if change.status != "pending":
            raise PromptValidationError("Prompt change is not pending")
        approved = await session.scalar(
            select(Approval.id)
            .join(Task, Task.id == Approval.task_id)
            .where(
                Approval.task_id == change.task_id,
                Approval.approval_type == ApprovalType.CHANGE.value,
                Approval.subject == change.id,
                Approval.status == ApprovalStatus.APPROVED.value,
                Task.user_id == user_id,
            )
            .limit(1)
        )
        if approved is None:
            raise PromptValidationError("Approved prompt change is required")
        self.module_for_filename(change.target_name)
        self.validate_content(change.candidate_content)
        current = self._read_managed(change.target_name)
        if _checksum(current) != change.base_checksum:
            change.status = "stale"
            await session.flush()
            raise PromptValidationError("Prompt base checksum changed")
        self._write_managed(change.target_name, change.candidate_content)
        session.add(
            EvolutionVersion(
                change_id=change.id,
                user_id=user_id,
                target_name=change.target_name,
                previous_checksum=change.base_checksum,
                new_checksum=change.candidate_checksum,
                previous_content=current,
                new_content=change.candidate_content,
                action="apply",
            )
        )
        change.status = "applied"
        change.applied_at = utc_now()
        await session.flush()
        return change

    async def rollback(
        self, *, session: AsyncSession, change_id: str, user_id: str
    ) -> EvolutionChange:
        """Rollback a previously applied managed prompt change.

        Args:
            session: 当前数据库异步会话。
            change_id: 用于执行当前操作的 change id 参数。
            user_id: 目标用户 ID。
        """
        change = await self._owned_change(session, change_id, user_id)
        if change.status != "applied":
            raise PromptValidationError(
                "Only applied prompt changes can be rolled back"
            )
        applied = await session.scalar(
            select(EvolutionVersion)
            .where(
                EvolutionVersion.change_id == change.id,
                EvolutionVersion.action == "apply",
            )
            .order_by(EvolutionVersion.created_at.desc())
            .limit(1)
        )
        if applied is None:
            raise PromptValidationError("Applied prompt version is unavailable")
        current = self._read_managed(change.target_name)
        if _checksum(current) != applied.new_checksum:
            raise PromptValidationError("Prompt target changed after apply")
        if applied.previous_content:
            self._write_managed(change.target_name, applied.previous_content)
        else:
            (self.managed_root / change.target_name).unlink(missing_ok=True)
        session.add(
            EvolutionVersion(
                change_id=change.id,
                user_id=user_id,
                target_name=change.target_name,
                previous_checksum=applied.new_checksum,
                new_checksum=applied.previous_checksum,
                previous_content=current,
                new_content=applied.previous_content,
                action="rollback",
            )
        )
        change.status = "rolled_back"
        await session.flush()
        return change

    def list_versions(self) -> dict[str, object]:
        """执行当前组件定义的业务处理逻辑。"""
        versions_dir = self.managed_root / ".versions"
        if not versions_dir.exists():
            return {"versions": []}
        return {
            "versions": sorted(path.name for path in versions_dir.glob("*.json"))[:100]
        }

    def module_for_filename(self, filename: str) -> PromptModuleName:
        """Resolve a managed prompt filename to its module name.

        Args:
            filename: 用于执行当前操作的 filename 参数。
        """
        if filename not in FILE_TO_MODULE:
            raise PromptValidationError("Unknown prompt module")
        return FILE_TO_MODULE[filename]

    def validate_module_name(self, name: str) -> PromptModuleName:
        """Validate and normalize a prompt module name.

        Args:
            name: 目标对象或能力的名称。
        """
        key = name.strip().upper()
        if key not in MODULE_FILES:
            raise PromptValidationError("Unknown prompt module")
        return key  # type: ignore[return-value]

    def validate_content(self, content: str) -> str:
        """Validate managed prompt content for size and safety.

        Args:
            content: 用于执行当前操作的 content 参数。
        """
        if not isinstance(content, str) or not content.strip():
            raise PromptValidationError("Prompt content is required")
        if len(content.encode("utf-8")) > self.max_module_bytes:
            raise PromptValidationError("Prompt content is too large")
        if _SECRET_LIKE.search(content):
            raise PromptValidationError("Prompt content contains secret-like text")
        if _POLICY_DOWNGRADE.search(content):
            raise PromptValidationError(
                "Prompt content attempts to weaken governance policy"
            )
        return content

    def _read_managed(self, filename: str) -> str:
        """Read managed prompt content if it exists and is safe.

        Args:
            filename: 用于执行当前操作的 filename 参数。
        """
        path = self.managed_root / filename
        if not path.exists():
            return ""
        if not _safe_existing_file(path, self.managed_root, self.max_module_bytes):
            raise PromptValidationError("Managed prompt target is unsafe")
        return path.read_text(encoding="utf-8")

    def _write_managed(self, filename: str, content: str) -> None:
        """Write managed prompt content and a lightweight version marker.

        Args:
            filename: 用于执行当前操作的 filename 参数。
            content: 用于执行当前操作的 content 参数。
        """
        self.managed_root.mkdir(parents=True, exist_ok=True)
        path = (self.managed_root / filename).resolve()
        if not path.is_relative_to(self.managed_root):
            raise PromptValidationError("Managed prompt path escaped root")
        path.write_text(content, encoding="utf-8")
        versions = self.managed_root / ".versions"
        versions.mkdir(exist_ok=True)
        (versions / f"{filename}.{_checksum(content)[:12]}.json").write_text(
            json.dumps(
                {"filename": filename, "fingerprint": _checksum(content)},
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _latest_version(self, filename: str) -> str | None:
        """Return the newest version marker file for a managed prompt.

        Args:
            filename: 用于执行当前操作的 filename 参数。
        """
        versions = self.managed_root / ".versions"
        if not versions.exists():
            return None
        matches = sorted(versions.glob(f"{filename}.*.json"), key=lambda p: p.name)
        return matches[-1].name if matches else None

    async def _owned_change(
        self, session: AsyncSession, change_id: str, user_id: str
    ) -> EvolutionChange:
        """Load an owned prompt evolution change.

        Args:
            session: 当前数据库异步会话。
            change_id: 用于执行当前操作的 change id 参数。
            user_id: 目标用户 ID。
        """
        change = await session.scalar(
            select(EvolutionChange).where(
                EvolutionChange.id == change_id,
                EvolutionChange.user_id == user_id,
                EvolutionChange.target_kind == "prompt",
            )
        )
        if change is None:
            raise PromptValidationError("Prompt change is unavailable")
        self.module_for_filename(change.target_name)
        return change


def _safe_existing_file(path: Path, root: Path, max_bytes: int) -> bool:
    """Return whether a file is within root, regular, non-symlink, and bounded.

    Args:
        path: 用于执行当前操作的 path 参数。
        root: 用于执行当前操作的 root 参数。
        max_bytes: 用于执行当前操作的 max bytes 参数。
    """
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    return (
        resolved.is_relative_to(root)
        and resolved.is_file()
        and not resolved.is_symlink()
        and resolved.stat().st_size <= max_bytes
    )


def _checksum(content: str) -> str:
    """Return a stable checksum for prompt content.

    Args:
        content: 用于执行当前操作的 content 参数。
    """
    return sha256(content.encode("utf-8")).hexdigest()
