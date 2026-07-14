from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent_harness import (
    ManagedSkillRecord,
    ManagedSkillStore,
    ManagedSkillStoreError,
)
from packages.capabilities import discover_skill_metadata

from .repositories import SkillAuditRepository
from .services import UserNotFoundError


SkillSource = Literal["builtin", "managed"]


@dataclass(frozen=True)
class SkillInventoryItem:
    name: str
    display_name: str
    summary: str
    version: str
    source: SkillSource
    enabled: bool
    manageable: bool


class SkillLifecycleError(ValueError):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class SkillLifecycleService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        store: ManagedSkillStore,
        refresh_registry: Callable[[], None],
    ) -> None:
        self.session = session
        self.store = store
        self.refresh_registry = refresh_registry
        self.audit_repository = SkillAuditRepository(session)

    def list_skills(self) -> tuple[SkillInventoryItem, ...]:
        items = [
            SkillInventoryItem(
                name=metadata.id.removeprefix("skill."),
                display_name=metadata.display_name,
                summary=metadata.summary,
                version="bundled",
                source="builtin",
                enabled=True,
                manageable=False,
            )
            for metadata in discover_skill_metadata(self.store.builtin_root)
        ]
        builtin_names = {item.name for item in items}
        items.extend(
            self._inventory_item(record)
            for record in self.store.list_managed()
            if record.name not in builtin_names
        )
        return tuple(sorted(items, key=lambda item: item.name))

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        display_name: str,
        summary: str,
        instructions: str,
    ) -> SkillInventoryItem:
        record = await self._mutate(
            user_id=user_id,
            skill_name=name,
            action="create",
            operation=lambda: self.store.create(
                name=name,
                display_name=display_name,
                summary=summary,
                instructions=instructions,
            ),
        )
        return self._inventory_item(record)

    async def install(self, *, user_id: str, package: bytes) -> SkillInventoryItem:
        record = await self._mutate(
            user_id=user_id,
            skill_name=None,
            action="install",
            operation=lambda: self.store.install(package),
        )
        return self._inventory_item(record)

    async def set_enabled(
        self,
        *,
        user_id: str,
        name: str,
        enabled: bool,
    ) -> SkillInventoryItem:
        action = "enable" if enabled else "disable"
        record = await self._mutate(
            user_id=user_id,
            skill_name=name,
            action=action,
            operation=lambda: self.store.set_enabled(name, enabled=enabled),
        )
        return self._inventory_item(record)

    async def uninstall(self, *, user_id: str, name: str) -> None:
        await self._mutate(
            user_id=user_id,
            skill_name=name,
            action="uninstall",
            operation=lambda: self.store.uninstall(name),
        )

    async def _mutate(
        self,
        *,
        user_id: str,
        skill_name: str | None,
        action: str,
        operation: Callable[[], ManagedSkillRecord],
    ) -> ManagedSkillRecord:
        if not await self.audit_repository.user_exists(user_id):
            raise UserNotFoundError(f"User not found: {user_id}")

        audit = await self.audit_repository.create_started(
            actor_user_id=user_id,
            skill_name=skill_name,
            action=action,
        )
        await self.session.commit()

        try:
            record = operation()
            self.refresh_registry()
        except ManagedSkillStoreError as exc:
            await self.audit_repository.finish(
                audit.id,
                status="failed",
                skill_name=skill_name,
                version=None,
                error_code=exc.code,
            )
            await self.session.commit()
            raise SkillLifecycleError(
                code=exc.code,
                message=str(exc),
                status_code=exc.status_code,
            ) from exc
        except Exception as exc:
            await self.audit_repository.finish(
                audit.id,
                status="failed",
                skill_name=skill_name,
                version=None,
                error_code="skill_operation_failed",
            )
            await self.session.commit()
            raise SkillLifecycleError(
                code="skill_operation_failed",
                message="Skill operation failed",
                status_code=500,
            ) from exc

        await self.audit_repository.finish(
            audit.id,
            status="succeeded",
            skill_name=record.name,
            version=record.version,
            error_code=None,
        )
        await self.session.commit()
        return record

    @staticmethod
    def _inventory_item(record: ManagedSkillRecord) -> SkillInventoryItem:
        return SkillInventoryItem(
            name=record.name,
            display_name=record.display_name,
            summary=record.summary,
            version=record.version,
            source="managed",
            enabled=record.enabled,
            manageable=True,
        )
