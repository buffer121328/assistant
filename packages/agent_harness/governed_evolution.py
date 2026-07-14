from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import (
    Approval,
    ApprovalStatus,
    ApprovalType,
    EvolutionChange,
    EvolutionVersion,
    Task,
    TaskStatus,
    utc_now,
)
from .skill_store import (
    ManagedSkillImmutableError,
    ManagedSkillNotFoundError,
    ManagedSkillStore,
)


TargetKind = Literal["prompt", "skill", "skill_package"]
_SAFE_PROMPT = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}\.md$")
_SAFE_SKILL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SAFE_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}\.zip$")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+"
)


class EvolutionError(ValueError):
    pass


class EvolutionValidationError(EvolutionError):
    pass


class EvolutionApprovalError(EvolutionError):
    pass


class EvolutionStaleError(EvolutionError):
    pass


class GovernedEvolutionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        prompt_root: Path,
        skill_root: Path,
        skill_store: ManagedSkillStore | None = None,
        skill_package_root: Path | None = None,
    ) -> None:
        self.session = session
        self.prompt_root = prompt_root.expanduser().resolve()
        self.skill_root = skill_root.expanduser().resolve()
        self.skill_store = skill_store
        self.skill_package_root = (
            skill_package_root.expanduser().resolve()
            if skill_package_root is not None
            else None
        )

    async def propose(
        self,
        *,
        task_id: str,
        user_id: str,
        target_kind: TargetKind,
        target_name: str,
        candidate_content: str,
        evidence: str,
    ) -> EvolutionChange:
        task = await self.session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        if task is None:
            raise EvolutionValidationError("Evolution task is unavailable")
        target = self._target(target_kind, target_name)
        current = self._read_target(target)
        candidate = self._validate_candidate(candidate_content)
        safe_evidence = evidence.strip()[:2_000]
        if not safe_evidence:
            raise EvolutionValidationError("Evolution evidence is required")
        change = EvolutionChange(
            task_id=task_id,
            user_id=user_id,
            target_kind=target_kind,
            target_name=target_name,
            base_checksum=_checksum(current),
            candidate_checksum=_checksum(candidate),
            candidate_content=candidate,
            evidence=safe_evidence,
            validation_result=json.dumps(
                {
                    "path_safe": True,
                    "size_safe": True,
                    "sensitive_content": False,
                    "deterministic_validation": "passed",
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            status="pending",
        )
        self.session.add(change)
        await self.session.flush()
        self.session.add(
            Approval(
                task_id=task_id,
                status=ApprovalStatus.PENDING.value,
                tool_name="agent.change",
                approval_type=ApprovalType.CHANGE.value,
                subject=change.id,
                request_summary=(
                    f"受治理变更：{target_kind}/{target_name}；"
                    f"candidate={change.candidate_checksum[:12]}"
                ),
            )
        )
        if task.status in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}:
            task.status = TaskStatus.WAITING_APPROVAL.value
            task.result_text = "候选变更已验证，等待人工审批。"
            task.error_message = None
        await self.session.commit()
        await self.session.refresh(change)
        return change

    async def propose_append(
        self,
        *,
        task_id: str,
        user_id: str,
        target_kind: TargetKind,
        target_name: str,
        guidance: str,
        evidence: str,
    ) -> EvolutionChange:
        target = self._target(target_kind, target_name)
        current = self._read_target(target)
        bounded_guidance = guidance.strip()[:2_000]
        if not bounded_guidance:
            raise EvolutionValidationError("Evolution guidance is required")
        candidate = f"{current.rstrip()}\n\n## Managed evolution guidance\n\n{bounded_guidance}\n"
        return await self.propose(
            task_id=task_id,
            user_id=user_id,
            target_kind=target_kind,
            target_name=target_name,
            candidate_content=candidate,
            evidence=evidence,
        )

    async def propose_skill_install(
        self,
        *,
        task_id: str,
        user_id: str,
        package_name: str,
        evidence: str,
    ) -> EvolutionChange:
        task = await self.session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        if task is None:
            raise EvolutionValidationError("Evolution task is unavailable")
        package_path = self._skill_package_path(package_name)
        package = package_path.read_bytes()
        skill_name = self._validate_skill_package(package)
        safe_evidence = evidence.strip()[:2_000]
        if not safe_evidence:
            raise EvolutionValidationError("Evolution evidence is required")
        package_checksum = _bytes_checksum(package)
        candidate_content = json.dumps(
            {"package_name": package_name, "skill_name": skill_name},
            separators=(",", ":"),
            sort_keys=True,
        )
        change = EvolutionChange(
            task_id=task_id,
            user_id=user_id,
            target_kind="skill_package",
            target_name=package_name,
            base_checksum=_checksum(""),
            candidate_checksum=package_checksum,
            candidate_content=candidate_content,
            evidence=safe_evidence,
            validation_result=json.dumps(
                {
                    "local_package": True,
                    "script_free": True,
                    "store_validation": "passed",
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            status="pending",
        )
        self.session.add(change)
        await self.session.flush()
        self.session.add(
            Approval(
                task_id=task_id,
                status=ApprovalStatus.PENDING.value,
                tool_name="agent.change",
                approval_type=ApprovalType.CHANGE.value,
                subject=change.id,
                request_summary=(
                    f"受治理本地 Skill 安装：{skill_name}；"
                    f"package={package_checksum[:12]}"
                ),
            )
        )
        if task.status in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}:
            task.status = TaskStatus.WAITING_APPROVAL.value
            task.result_text = "Skill 候选包已验证，等待人工审批。"
            task.error_message = None
        await self.session.commit()
        await self.session.refresh(change)
        return change

    async def apply(self, *, change_id: str, user_id: str) -> EvolutionChange:
        change = await self._owned_change(change_id, user_id)
        if change.status != "pending":
            raise EvolutionValidationError("Evolution change is not pending")
        approved = await self.session.scalar(
            select(Approval.id)
            .join(Task, Task.id == Approval.task_id)
            .where(
                Approval.task_id == change.task_id,
                Approval.approval_type == ApprovalType.CHANGE.value,
                Approval.subject == change.id,
                Approval.status == ApprovalStatus.APPROVED.value,
                Approval.decided_by_user_id == user_id,
                Task.user_id == user_id,
            )
            .limit(1)
        )
        if approved is None:
            raise EvolutionApprovalError("Exact change approval is required")
        if change.target_kind == "skill_package":
            return await self._apply_skill_package(change, user_id=user_id)
        target = self._target(change.target_kind, change.target_name)
        current = self._read_target(target)
        if _checksum(current) != change.base_checksum:
            change.status = "stale"
            await self.session.commit()
            raise EvolutionStaleError("Evolution base checksum changed")
        self._atomic_write(target, change.candidate_content)
        self.session.add(
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
        await self.session.commit()
        await self.session.refresh(change)
        return change

    async def rollback(self, *, change_id: str, user_id: str) -> EvolutionChange:
        change = await self._owned_change(change_id, user_id)
        if change.status != "applied":
            raise EvolutionValidationError("Only an applied change can be rolled back")
        applied = await self.session.scalar(
            select(EvolutionVersion)
            .where(
                EvolutionVersion.change_id == change.id,
                EvolutionVersion.action == "apply",
            )
            .order_by(EvolutionVersion.created_at.desc())
            .limit(1)
        )
        if applied is None:
            raise EvolutionValidationError("Applied version is unavailable")
        if change.target_kind == "skill_package":
            return await self._rollback_skill_package(
                change,
                applied=applied,
                user_id=user_id,
            )
        target = self._target(change.target_kind, change.target_name)
        current = self._read_target(target)
        if _checksum(current) != applied.new_checksum:
            raise EvolutionStaleError("Evolution target changed after apply")
        self._atomic_write(target, applied.previous_content)
        self.session.add(
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
        await self.session.commit()
        await self.session.refresh(change)
        return change

    async def _apply_skill_package(
        self,
        change: EvolutionChange,
        *,
        user_id: str,
    ) -> EvolutionChange:
        store = self._required_skill_store()
        package_path = self._skill_package_path(change.target_name)
        package = package_path.read_bytes()
        if _bytes_checksum(package) != change.candidate_checksum:
            change.status = "stale"
            await self.session.commit()
            raise EvolutionStaleError("Skill package checksum changed")
        record = store.install(package)
        if record.enabled:
            raise EvolutionValidationError("Installed Skill must remain disabled")
        self.session.add(
            EvolutionVersion(
                change_id=change.id,
                user_id=user_id,
                target_name=record.name,
                previous_checksum=change.base_checksum,
                new_checksum=change.candidate_checksum,
                previous_content="",
                new_content=change.candidate_content,
                action="apply",
            )
        )
        change.status = "applied"
        change.applied_at = utc_now()
        await self.session.commit()
        await self.session.refresh(change)
        return change

    async def _rollback_skill_package(
        self,
        change: EvolutionChange,
        *,
        applied: EvolutionVersion,
        user_id: str,
    ) -> EvolutionChange:
        store = self._required_skill_store()
        payload = json.loads(change.candidate_content)
        skill_name = payload.get("skill_name") if isinstance(payload, dict) else None
        if not isinstance(skill_name, str):
            raise EvolutionValidationError("Installed Skill metadata is invalid")
        record = store.get(skill_name)
        store.uninstall(record.name)
        self.session.add(
            EvolutionVersion(
                change_id=change.id,
                user_id=user_id,
                target_name=record.name,
                previous_checksum=applied.new_checksum,
                new_checksum=applied.previous_checksum,
                previous_content=change.candidate_content,
                new_content="",
                action="rollback",
            )
        )
        change.status = "rolled_back"
        await self.session.commit()
        await self.session.refresh(change)
        return change

    async def _owned_change(self, change_id: str, user_id: str) -> EvolutionChange:
        change = await self.session.scalar(
            select(EvolutionChange).where(
                EvolutionChange.id == change_id,
                EvolutionChange.user_id == user_id,
            )
        )
        if change is None:
            raise EvolutionValidationError("Evolution change is unavailable")
        return change

    def _skill_package_path(self, package_name: str) -> Path:
        root = self.skill_package_root
        if (
            root is None
            or not root.is_dir()
            or root.is_symlink()
            or not _SAFE_PACKAGE.fullmatch(package_name)
        ):
            raise EvolutionValidationError("Skill package root or name is invalid")
        candidate = root / package_name
        if candidate.is_symlink():
            raise EvolutionValidationError("Skill package must not be a symlink")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise EvolutionValidationError("Skill package is unavailable") from exc
        if resolved.parent != root or not resolved.is_file():
            raise EvolutionValidationError("Skill package escaped managed root")
        return resolved

    def _validate_skill_package(self, package: bytes) -> str:
        store = self._required_skill_store()
        with tempfile.TemporaryDirectory(prefix="agent-skill-validation-") as directory:
            validation_store = ManagedSkillStore(
                builtin_root=store.builtin_root,
                managed_root=Path(directory),
            )
            try:
                record = validation_store.install(package)
            except Exception as exc:
                raise EvolutionValidationError("Skill package validation failed") from exc
        try:
            store.get(record.name)
        except ManagedSkillNotFoundError:
            return record.name
        except ManagedSkillImmutableError as exc:
            raise EvolutionValidationError("Built-in Skill is immutable") from exc
        raise EvolutionValidationError("Managed Skill already exists")

    def _required_skill_store(self) -> ManagedSkillStore:
        if self.skill_store is None:
            raise EvolutionValidationError("Managed Skill store is unavailable")
        return self.skill_store

    def _target(self, target_kind: str, target_name: str) -> Path:
        if target_kind == "prompt" and _SAFE_PROMPT.fullmatch(target_name):
            root = self.prompt_root
            target = root / target_name
        elif target_kind == "skill" and _SAFE_SKILL.fullmatch(target_name):
            root = self.skill_root
            target = root / target_name / "SKILL.md"
        else:
            raise EvolutionValidationError("Evolution target is not managed")
        if not root.is_dir() or root.is_symlink() or target.is_symlink():
            raise EvolutionValidationError("Evolution managed root is unavailable")
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise EvolutionValidationError("Evolution target is unavailable") from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise EvolutionValidationError("Evolution target escaped managed root")
        return resolved

    @staticmethod
    def _read_target(target: Path) -> str:
        if target.stat().st_size > 128 * 1024:
            raise EvolutionValidationError("Evolution target is too large")
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise EvolutionValidationError("Evolution target is unreadable") from exc

    @staticmethod
    def _validate_candidate(content: str) -> str:
        if not isinstance(content, str):
            raise EvolutionValidationError("Evolution candidate must be text")
        if not content.strip() or len(content.encode("utf-8")) > 128 * 1024:
            raise EvolutionValidationError("Evolution candidate size is invalid")
        if _SENSITIVE_ASSIGNMENT.search(content) or "PRIVATE KEY-----" in content:
            raise EvolutionValidationError("Evolution candidate contains sensitive data")
        return content

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        descriptor, name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def _checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _bytes_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
