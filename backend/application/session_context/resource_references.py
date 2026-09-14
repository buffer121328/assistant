from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from mimetypes import guess_type
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.artifact_lifecycle import (
    ArtifactLifecycleError,
    ArtifactLifecycleService,
)
from application.session_context.conversations import ConversationService
from domain.models import Conversation, ConversationResourceReference, Task, User, utc_now
from tools.builtin.artifacts import ArtifactStore

_MAX_SOURCE_REF_LENGTH = 4096
_MAX_REFERENCE_IDS = 32
_MAX_MENTION_LIMIT = 50
_MAX_UPLOADED_FILE_BYTES = 25 * 1024 * 1024
_MAX_EXTERNAL_PROVIDER_LENGTH = 64
_MAX_EXTERNAL_ID_LENGTH = 512
_MAX_EXTERNAL_VERSION_LENGTH = 512
_DEFAULT_UPLOADED_RESOURCES_ROOT = Path("var/resource-inputs")
_DEFAULT_ARTIFACTS_ROOT = Path("var/artifacts")


class ResourceReferenceError(Exception):
    """定义当前组件可安全处理的错误类型。"""

    def __init__(self, code: str, status_code: int) -> None:
        """Store only a stable error code and public HTTP status.

        Args:
            code: 可安全返回给调用方的错误码。
            status_code: 可安全返回给调用方的 HTTP 状态码。
        """
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class ResourceReferenceView:
    """定义当前组件的职责和边界。"""

    id: str  # id 对应的数据字段。
    conversation_id: str  # conversation_id 对应的数据字段。
    resource_kind: str  # resource_kind 对应的数据字段。
    provider: str  # provider 对应的数据字段。
    display_name: str  # display_name 对应的数据字段。
    version: str  # version 对应的数据字段。
    content_hash: str | None  # content_hash 对应的数据字段。
    size_bytes: int  # size_bytes 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    tenant_id: str  # tenant_id 对应的数据字段。
    organization_id: str | None  # organization_id 对应的数据字段。
    owner_type: str  # owner_type 对应的数据字段。
    owner_id: str  # owner_id 对应的数据字段。
    visibility: str  # visibility 对应的数据字段。
    sensitivity: str  # sensitivity 对应的数据字段。
    status: str  # status 对应的数据字段。
    pinned: bool  # pinned 对应的数据字段。
    created_at: datetime  # created_at 对应的数据字段。
    updated_at: datetime  # updated_at 对应的数据字段。
    last_resolved_at: datetime | None  # last_resolved_at 对应的数据字段。


@dataclass(frozen=True)
class UploadedResourceDownload:
    """One verified managed-upload delivery result without storage metadata."""

    path: Path
    filename: str
    media_type: str


@dataclass(frozen=True)
class ResolvedResourceReference:
    """定义当前组件的职责和边界。"""

    id: str  # id 对应的数据字段。
    conversation_id: str  # conversation_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    tenant_id: str  # tenant_id 对应的数据字段。
    organization_id: str | None  # organization_id 对应的数据字段。
    owner_type: str  # owner_type 对应的数据字段。
    owner_id: str  # owner_id 对应的数据字段。
    visibility: str  # visibility 对应的数据字段。
    version: str  # version 对应的数据字段。


@dataclass(frozen=True)
class WorkspaceResourceReference:
    """定义当前组件的职责和边界。"""

    id: str  # id 对应的数据字段。
    display_name: str  # display_name 对应的数据字段。
    source_path: Path  # source_path 对应的数据字段。
    version: str  # version 对应的数据字段。
    device: int  # device 对应的数据字段。
    inode: int  # inode 对应的数据字段。
    size_bytes: int  # size_bytes 对应的数据字段。
    modified_ns: int  # modified_ns 对应的数据字段。


@dataclass(frozen=True)
class ExternalRecordResolution:
    """定义当前组件的职责和边界。"""

    version: str  # version 对应的数据字段。


class ExternalRecordResolver(Protocol):
    """定义当前组件的接口契约。"""

    async def resolve(
        self,
        *,
        provider: str,
        external_resource_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ExternalRecordResolution:
        """Return a current authorized version without exposing provider credentials.

        Args:
            provider: 用于执行当前操作的 provider 参数。
            external_resource_id: 用于执行当前操作的 external resource id 参数。
            user_id: 目标用户 ID。
            conversation_id: 目标会话 ID。
        """


class ExternalRecordResolverRegistry:
    """定义当前组件的职责和边界。"""

    def __init__(self) -> None:
        """初始化当前组件所需的依赖和状态。"""
        self._resolvers: dict[str, ExternalRecordResolver] = {}

    def register(self, provider: str, resolver: ExternalRecordResolver) -> None:
        """Register one explicit provider resolver under its bounded provider key.

        Args:
            provider: 用于执行当前操作的 provider 参数。
            resolver: 用于执行当前操作的 resolver 参数。
        """
        safe_provider = _normalize_external_provider(provider)
        self._resolvers[safe_provider] = resolver

    def get(self, provider: str) -> ExternalRecordResolver | None:
        """Return an optional resolver without treating an unknown provider as valid.

        Args:
            provider: 用于执行当前操作的 provider 参数。
        """
        return self._resolvers.get(provider)


DEFAULT_EXTERNAL_RECORD_RESOLVERS = ExternalRecordResolverRegistry()


@dataclass(frozen=True)
class _LocalFileMetadata:
    """定义当前组件的职责和边界。"""

    source_ref: str  # source_ref 对应的数据字段。
    locator_hash: str  # locator_hash 对应的数据字段。
    display_name: str  # display_name 对应的数据字段。
    version: str  # version 对应的数据字段。
    size_bytes: int  # size_bytes 对应的数据字段。
    device: int  # device 对应的数据字段。
    inode: int  # inode 对应的数据字段。
    modified_ns: int  # modified_ns 对应的数据字段。


@dataclass(frozen=True)
class _ManagedUploadMetadata:
    """定义当前组件的职责和边界。"""

    source_path: Path  # source_path 对应的数据字段。
    content_hash: str  # content_hash 对应的数据字段。
    version: str  # version 对应的数据字段。
    size_bytes: int  # size_bytes 对应的数据字段。
    device: int  # device 对应的数据字段。
    inode: int  # inode 对应的数据字段。
    modified_ns: int  # modified_ns 对应的数据字段。


class ResourceReferenceService:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        uploaded_resources_root: Path = _DEFAULT_UPLOADED_RESOURCES_ROOT,
        artifacts_root: Path = _DEFAULT_ARTIFACTS_ROOT,
        external_record_resolvers: ExternalRecordResolverRegistry | None = None,
    ) -> None:
        """Bind resource lifecycle operations to the caller's transaction.

        Args:
            session: 当前数据库异步会话。
            uploaded_resources_root: 用于执行当前操作的 uploaded resources root 参数。
            artifacts_root: 用于执行当前操作的 artifacts root 参数。
            external_record_resolvers: 用于执行当前操作的 external record resolvers 参数。
        """
        self.session = session
        self.uploaded_resources_root = uploaded_resources_root
        self.artifacts_root = artifacts_root
        self.external_record_resolvers = (
            external_record_resolvers or DEFAULT_EXTERNAL_RECORD_RESOLVERS
        )

    async def attach_local_file(
        self,
        *,
        conversation_id: str,
        user_id: str,
        source_ref: str,
        sensitivity: str = "normal",
        pinned: bool = False,
    ) -> ResourceReferenceView:
        """Attach or reactivate one explicit regular file without reading content.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            source_ref: 用于执行当前操作的 source ref 参数。
            sensitivity: 用于执行当前操作的 sensitivity 参数。
            pinned: 用于执行当前操作的 pinned 参数。
        """
        conversation = await ConversationService(self.session).get_owned(
            conversation_id=conversation_id,
            user_id=user_id,
            active_only=True,
        )
        user = await self.session.get(User, user_id)
        if user is None:
            raise ResourceReferenceError("resource_owner_not_found", 404)
        metadata = _local_file_metadata(source_ref)
        safe_sensitivity = sensitivity.strip().lower()
        if safe_sensitivity not in {"normal", "sensitive", "restricted"}:
            raise ResourceReferenceError("resource_sensitivity_invalid", 422)
        item = await self.session.scalar(
            select(ConversationResourceReference).where(
                ConversationResourceReference.conversation_id == conversation_id,
                ConversationResourceReference.resource_kind == "local_file",
                ConversationResourceReference.source_locator_hash
                == metadata.locator_hash,
            )
        )
        now = utc_now()
        if item is None:
            item = ConversationResourceReference(
                conversation_id=conversation_id,
                resource_kind="local_file",
                provider="local",
                display_name=metadata.display_name,
                source_ref=metadata.source_ref,
                source_locator_hash=metadata.locator_hash,
                version=metadata.version,
                content_hash=None,
                size_bytes=metadata.size_bytes,
                user_id=user_id,
                tenant_id=conversation.tenant_id,
                organization_id=conversation.organization_id,
                owner_type=conversation.owner_type,
                owner_id=conversation.owner_id,
                visibility=conversation.visibility,
                sensitivity=safe_sensitivity,
                status="attached",
                pinned=pinned,
                created_by=user_id,
                last_resolved_at=now,
            )
            self.session.add(item)
        else:
            if item.user_id != user_id:
                raise ResourceReferenceError("resource_reference_not_found", 404)
            item.display_name = metadata.display_name
            item.source_ref = metadata.source_ref
            item.version = metadata.version
            item.size_bytes = metadata.size_bytes
            item.sensitivity = safe_sensitivity
            item.status = "attached"
            item.pinned = pinned
            item.last_resolved_at = now
        await self.session.commit()
        await self.session.refresh(item)
        return self._view(item)

    async def attach_uploaded_file(
        self,
        *,
        conversation_id: str,
        user_id: str,
        filename: str,
        content: bytes,
        sensitivity: str = "normal",
        pinned: bool = False,
    ) -> ResourceReferenceView:
        """Persist one bounded explicit upload under a backend-owned opaque reference.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            filename: 用于执行当前操作的 filename 参数。
            content: 用于执行当前操作的 content 参数。
            sensitivity: 用于执行当前操作的 sensitivity 参数。
            pinned: 用于执行当前操作的 pinned 参数。
        """
        safe_filename = _normalize_uploaded_filename(filename)
        if not content or len(content) > _MAX_UPLOADED_FILE_BYTES:
            raise ResourceReferenceError("resource_upload_invalid", 422)
        content_hash = sha256(content).hexdigest()
        locator_hash = sha256(
            f"uploaded_file:{safe_filename}:{content_hash}".encode("utf-8")
        ).hexdigest()
        conversation = await ConversationService(self.session).get_owned(
            conversation_id=conversation_id,
            user_id=user_id,
            active_only=True,
        )
        user = await self.session.get(User, user_id)
        if user is None:
            raise ResourceReferenceError("resource_owner_not_found", 404)
        safe_sensitivity = _normalize_sensitivity(sensitivity)
        item = await self.session.scalar(
            select(ConversationResourceReference).where(
                ConversationResourceReference.conversation_id == conversation_id,
                ConversationResourceReference.resource_kind == "uploaded_file",
                ConversationResourceReference.source_locator_hash == locator_hash,
            )
        )
        if item is not None and item.user_id != user_id:
            raise ResourceReferenceError("resource_reference_not_found", 404)

        created_path: Path | None = None
        now = utc_now()
        if item is None:
            storage_ref = uuid4().hex
            created_path = _managed_upload_path(self.uploaded_resources_root, storage_ref)
            try:
                created_path.parent.mkdir(parents=True, exist_ok=True)
                created_path.write_bytes(content)
                item = ConversationResourceReference(
                    conversation_id=conversation_id,
                    resource_kind="uploaded_file",
                    provider="managed_upload",
                    display_name=safe_filename,
                    source_ref=storage_ref,
                    source_locator_hash=locator_hash,
                    version=content_hash,
                    content_hash=content_hash,
                    size_bytes=len(content),
                    user_id=user_id,
                    tenant_id=conversation.tenant_id,
                    organization_id=conversation.organization_id,
                    owner_type=conversation.owner_type,
                    owner_id=conversation.owner_id,
                    visibility=conversation.visibility,
                    sensitivity=safe_sensitivity,
                    status="attached",
                    pinned=pinned,
                    created_by=user_id,
                    last_resolved_at=now,
                )
                self.session.add(item)
                await self.session.commit()
            except Exception:
                if created_path.exists():
                    created_path.unlink()
                raise
        else:
            item.display_name = safe_filename
            item.version = content_hash
            item.content_hash = content_hash
            item.size_bytes = len(content)
            item.sensitivity = safe_sensitivity
            item.status = "attached"
            item.pinned = pinned
            item.last_resolved_at = now
            await self.session.commit()
        await self.session.refresh(item)
        return self._view(item)

    async def import_local_file(
        self,
        *,
        conversation_id: str,
        user_id: str,
        source_ref: str,
        sensitivity: str = "normal",
        pinned: bool = False,
    ) -> ResourceReferenceView:
        """Copy one selected local file into managed upload storage.

        The desktop chooser already supplies the explicit path used by the
        existing local-file reference flow. Importing it here makes the
        resulting Conversation resource independent of that original path and
        eligible for private Workspace discovery.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 当前用户 ID。
            source_ref: 用户明确选择的本地文件路径。
            sensitivity: 文件敏感级别。
            pinned: 是否固定在会话资料中。
        """
        metadata = _local_file_metadata(source_ref)
        if metadata.size_bytes > _MAX_UPLOADED_FILE_BYTES:
            raise ResourceReferenceError("resource_upload_invalid", 422)
        try:
            content = Path(metadata.source_ref).read_bytes()
        except OSError as exc:
            raise ResourceReferenceError("resource_upload_invalid", 422) from exc
        return await self.attach_uploaded_file(
            conversation_id=conversation_id,
            user_id=user_id,
            filename=metadata.display_name,
            content=content,
            sensitivity=sensitivity,
            pinned=pinned,
        )

    async def attach_artifact(
        self,
        *,
        conversation_id: str,
        user_id: str,
        artifact_id: str,
        sensitivity: str = "normal",
        pinned: bool = False,
    ) -> ResourceReferenceView:
        """Attach one already-governed Artifact by its stable identity only.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            artifact_id: 目标产物 ID。
            sensitivity: 用于执行当前操作的 sensitivity 参数。
            pinned: 用于执行当前操作的 pinned 参数。
        """
        conversation = await ConversationService(self.session).get_owned(
            conversation_id=conversation_id,
            user_id=user_id,
            active_only=True,
        )
        user = await self.session.get(User, user_id)
        if user is None:
            raise ResourceReferenceError("resource_owner_not_found", 404)
        safe_artifact_id = artifact_id.strip()
        if not safe_artifact_id or len(safe_artifact_id) > 64:
            raise ResourceReferenceError("resource_artifact_invalid", 422)
        try:
            artifact = await ArtifactLifecycleService(
                self.session,
                store=ArtifactStore(self.artifacts_root),
                auto_commit=False,
            ).resolve_context_input(
                artifact_id=safe_artifact_id,
                actor_user_id=user_id,
                conversation_id=conversation_id,
            )
        except ArtifactLifecycleError as exc:
            raise ResourceReferenceError("resource_reference_not_found", 404) from exc
        safe_sensitivity = _normalize_sensitivity(sensitivity)
        locator_hash = sha256(f"artifact:{artifact.id}".encode("utf-8")).hexdigest()
        item = await self.session.scalar(
            select(ConversationResourceReference).where(
                ConversationResourceReference.conversation_id == conversation_id,
                ConversationResourceReference.resource_kind == "artifact",
                ConversationResourceReference.source_locator_hash == locator_hash,
            )
        )
        now = utc_now()
        if item is None:
            item = ConversationResourceReference(
                conversation_id=conversation_id,
                resource_kind="artifact",
                provider="artifact",
                display_name=artifact.filename,
                source_ref=artifact.id,
                source_locator_hash=locator_hash,
                artifact_id=artifact.id,
                version=artifact.version,
                content_hash=artifact.content_hash,
                size_bytes=artifact.size_bytes,
                user_id=user_id,
                tenant_id=conversation.tenant_id,
                organization_id=conversation.organization_id,
                owner_type=conversation.owner_type,
                owner_id=conversation.owner_id,
                visibility=conversation.visibility,
                sensitivity=safe_sensitivity,
                status="attached",
                pinned=pinned,
                created_by=user_id,
                last_resolved_at=now,
            )
            self.session.add(item)
        else:
            if item.user_id != user_id:
                raise ResourceReferenceError("resource_reference_not_found", 404)
            item.display_name = artifact.filename
            item.artifact_id = artifact.id
            item.version = artifact.version
            item.content_hash = artifact.content_hash
            item.size_bytes = artifact.size_bytes
            item.sensitivity = safe_sensitivity
            item.status = "attached"
            item.pinned = pinned
            item.last_resolved_at = now
        await self.session.commit()
        await self.session.refresh(item)
        return self._view(item)

    async def attach_external_record(
        self,
        *,
        conversation_id: str,
        user_id: str,
        provider: str,
        external_resource_id: str,
        display_name: str,
        version: str | None = None,
        sensitivity: str = "normal",
        pinned: bool = False,
    ) -> ResourceReferenceView:
        """Attach a bounded opaque external identity without resolving provider content.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            provider: 用于执行当前操作的 provider 参数。
            external_resource_id: 用于执行当前操作的 external resource id 参数。
            display_name: 用于执行当前操作的 display name 参数。
            version: 用于执行当前操作的 version 参数。
            sensitivity: 用于执行当前操作的 sensitivity 参数。
            pinned: 用于执行当前操作的 pinned 参数。
        """
        conversation = await ConversationService(self.session).get_owned(
            conversation_id=conversation_id,
            user_id=user_id,
            active_only=True,
        )
        user = await self.session.get(User, user_id)
        if user is None:
            raise ResourceReferenceError("resource_owner_not_found", 404)
        safe_provider = _normalize_external_provider(provider)
        safe_external_id = _normalize_external_value(
            external_resource_id, _MAX_EXTERNAL_ID_LENGTH
        )
        safe_display_name = _normalize_display_name(display_name)
        safe_version = _normalize_external_value(
            version or "unspecified", _MAX_EXTERNAL_VERSION_LENGTH
        )
        safe_sensitivity = _normalize_sensitivity(sensitivity)
        locator_hash = sha256(
            f"external_record:{safe_provider}:{safe_external_id}".encode("utf-8")
        ).hexdigest()
        item = await self.session.scalar(
            select(ConversationResourceReference).where(
                ConversationResourceReference.conversation_id == conversation_id,
                ConversationResourceReference.resource_kind == "external_record",
                ConversationResourceReference.source_locator_hash == locator_hash,
            )
        )
        if item is None:
            item = ConversationResourceReference(
                conversation_id=conversation_id,
                resource_kind="external_record",
                provider=safe_provider,
                display_name=safe_display_name,
                source_ref=safe_external_id,
                source_locator_hash=locator_hash,
                version=safe_version,
                content_hash=None,
                size_bytes=0,
                user_id=user_id,
                tenant_id=conversation.tenant_id,
                organization_id=conversation.organization_id,
                owner_type=conversation.owner_type,
                owner_id=conversation.owner_id,
                visibility=conversation.visibility,
                sensitivity=safe_sensitivity,
                status="attached",
                pinned=pinned,
                created_by=user_id,
                last_resolved_at=None,
            )
            self.session.add(item)
        else:
            if item.user_id != user_id:
                raise ResourceReferenceError("resource_reference_not_found", 404)
            item.display_name = safe_display_name
            item.version = safe_version
            item.sensitivity = safe_sensitivity
            item.status = "attached"
            item.pinned = pinned
        await self.session.commit()
        await self.session.refresh(item)
        return self._view(item)

    async def list_space_context_candidates(
        self, *, conversation_id: str, user_id: str, limit: int = 20
    ) -> tuple[ResourceReferenceView, ...]:
        """List only the caller's active references from other Conversations in this Space.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            limit: 返回结果的最大数量。
        """
        target = await ConversationService(self.session).get_owned(
            conversation_id=conversation_id, user_id=user_id, active_only=True
        )
        if target.space_id is None:
            return ()
        source_conversation_ids = select(Conversation.id).where(
            Conversation.user_id == user_id,
            Conversation.space_id == target.space_id,
            Conversation.archived_at.is_(None),
            Conversation.id != target.id,
        )
        items = await self.session.scalars(
            select(ConversationResourceReference)
            .where(
                ConversationResourceReference.user_id == user_id,
                ConversationResourceReference.status == "attached",
                ConversationResourceReference.conversation_id.in_(source_conversation_ids),
            )
            .order_by(
                ConversationResourceReference.updated_at.desc(),
                ConversationResourceReference.id.desc(),
            )
            .limit(max(1, min(limit, _MAX_MENTION_LIMIT)))
        )
        return tuple(self._view(item) for item in items)

    async def attach_space_context_candidate(
        self,
        *,
        conversation_id: str,
        reference_id: str,
        user_id: str,
    ) -> ResourceReferenceView:
        """Copy a same-owner, same-Space reference into the active target Conversation.

        Args:
            conversation_id: 目标会话 ID。
            reference_id: 用于执行当前操作的 reference id 参数。
            user_id: 目标用户 ID。
        """
        target = await ConversationService(self.session).get_owned(
            conversation_id=conversation_id, user_id=user_id, active_only=True
        )
        if target.space_id is None:
            raise ResourceReferenceError("space_context_unavailable", 409)
        source = await self.session.scalar(
            select(ConversationResourceReference)
            .join(Conversation, Conversation.id == ConversationResourceReference.conversation_id)
            .where(
                ConversationResourceReference.id == reference_id,
                ConversationResourceReference.user_id == user_id,
                ConversationResourceReference.status == "attached",
                Conversation.user_id == user_id,
                Conversation.space_id == target.space_id,
                Conversation.archived_at.is_(None),
                Conversation.id != target.id,
            )
        )
        if source is None:
            raise ResourceReferenceError("space_context_reference_not_found", 404)
        item = await self.session.scalar(
            select(ConversationResourceReference).where(
                ConversationResourceReference.conversation_id == target.id,
                ConversationResourceReference.resource_kind == source.resource_kind,
                ConversationResourceReference.source_locator_hash == source.source_locator_hash,
            )
        )
        if item is None:
            item = ConversationResourceReference(
                conversation_id=target.id,
                resource_kind=source.resource_kind,
                provider=source.provider,
                display_name=source.display_name,
                source_ref=source.source_ref,
                source_locator_hash=source.source_locator_hash,
                artifact_id=source.artifact_id,
                version=source.version,
                content_hash=source.content_hash,
                size_bytes=source.size_bytes,
                user_id=user_id,
                tenant_id=target.tenant_id,
                organization_id=target.organization_id,
                owner_type=target.owner_type,
                owner_id=target.owner_id,
                visibility=target.visibility,
                sensitivity=source.sensitivity,
                status="attached",
                pinned=source.pinned,
                created_by=user_id,
                last_resolved_at=None,
            )
            self.session.add(item)
        else:
            item.status = "attached"
            item.version = source.version
            item.content_hash = source.content_hash
            item.size_bytes = source.size_bytes
            item.pinned = source.pinned
        await self.session.commit()
        await self.session.refresh(item)
        return self._view(item)

    async def list_attached(
        self, *, conversation_id: str, user_id: str
    ) -> tuple[ResourceReferenceView, ...]:
        """List only attached references inside one active owned Conversation.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
        """
        await ConversationService(self.session).get_owned(
            conversation_id=conversation_id,
            user_id=user_id,
            active_only=True,
        )
        items = await self.session.scalars(
            select(ConversationResourceReference)
            .where(
                ConversationResourceReference.conversation_id == conversation_id,
                ConversationResourceReference.user_id == user_id,
                ConversationResourceReference.status == "attached",
            )
            .order_by(
                ConversationResourceReference.updated_at.desc(),
                ConversationResourceReference.id.desc(),
            )
        )
        return tuple(self._view(item) for item in items)

    async def remove(
        self, *, conversation_id: str, reference_id: str, user_id: str
    ) -> ResourceReferenceView:
        """Soft-remove one owned reference without touching its source file.

        Args:
            conversation_id: 目标会话 ID。
            reference_id: 用于执行当前操作的 reference id 参数。
            user_id: 目标用户 ID。
        """
        await ConversationService(self.session).get_owned(
            conversation_id=conversation_id,
            user_id=user_id,
            active_only=True,
        )
        item = await self.session.scalar(
            select(ConversationResourceReference).where(
                ConversationResourceReference.id == reference_id,
                ConversationResourceReference.conversation_id == conversation_id,
                ConversationResourceReference.user_id == user_id,
            )
        )
        if item is None:
            raise ResourceReferenceError("resource_reference_not_found", 404)
        item.status = "removed"
        await self.session.commit()
        await self.session.refresh(item)
        return self._view(item)

    async def search_mentions(
        self,
        *,
        conversation_id: str,
        user_id: str,
        query: str = "",
        resource_kind: str | None = None,
        limit: int = 20,
    ) -> tuple[ResourceReferenceView, ...]:
        """Search display names only after applying owner and Conversation filters.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            query: 用于执行当前操作的 query 参数。
            resource_kind: 用于执行当前操作的 resource kind 参数。
            limit: 返回结果的最大数量。
        """
        await ConversationService(self.session).get_owned(
            conversation_id=conversation_id,
            user_id=user_id,
            active_only=True,
        )
        safe_query = query.strip().lower()[:200]
        safe_limit = max(1, min(limit, _MAX_MENTION_LIMIT))
        conditions = [
            ConversationResourceReference.conversation_id == conversation_id,
            ConversationResourceReference.user_id == user_id,
            ConversationResourceReference.status == "attached",
        ]
        if resource_kind:
            conditions.append(
                ConversationResourceReference.resource_kind == resource_kind
            )
        if safe_query:
            conditions.append(
                ConversationResourceReference.display_name.ilike(f"%{safe_query}%")
            )
        items = await self.session.scalars(
            select(ConversationResourceReference)
            .where(*conditions)
            .order_by(
                ConversationResourceReference.pinned.desc(),
                ConversationResourceReference.display_name.asc(),
                ConversationResourceReference.id.asc(),
            )
            .limit(safe_limit)
        )
        return tuple(self._view(item) for item in items)

    async def resolve_for_task(
        self,
        *,
        conversation_id: str,
        user_id: str,
        reference_ids: tuple[str, ...],
    ) -> tuple[ResolvedResourceReference, ...]:
        """Re-authorize and version selected references before Task persistence.

        Args:
            conversation_id: 目标会话 ID。
            user_id: 目标用户 ID。
            reference_ids: 用于执行当前操作的 reference ids 参数。
        """
        await ConversationService(self.session).get_owned(
            conversation_id=conversation_id,
            user_id=user_id,
            active_only=True,
        )
        normalized = tuple(
            dict.fromkeys(reference_id.strip() for reference_id in reference_ids)
        )
        if len(normalized) > _MAX_REFERENCE_IDS or any(not item for item in normalized):
            raise ResourceReferenceError("resource_reference_ids_invalid", 422)
        resolved: list[ResolvedResourceReference] = []
        for reference_id in normalized:
            item = await self.session.scalar(
                select(ConversationResourceReference).where(
                    ConversationResourceReference.id == reference_id,
                    ConversationResourceReference.conversation_id == conversation_id,
                    ConversationResourceReference.user_id == user_id,
                )
            )
            if item is None:
                raise ResourceReferenceError("resource_reference_not_found", 404)
            if item.status != "attached":
                raise ResourceReferenceError("resource_reference_unavailable", 409)
            await self._resolve_selected_item(item)
            item.last_resolved_at = utc_now()
            resolved.append(
                ResolvedResourceReference(
                    id=item.id,
                    conversation_id=item.conversation_id,
                    user_id=item.user_id,
                    tenant_id=item.tenant_id,
                    organization_id=item.organization_id,
                    owner_type=item.owner_type,
                    owner_id=item.owner_id,
                    visibility=item.visibility,
                    version=item.version,
                )
            )
        await self.session.flush()
        return tuple(resolved)

    async def resolve_uploaded_download(
        self,
        *,
        reference_id: str,
        user_id: str,
    ) -> UploadedResourceDownload:
        """Resolve one active managed upload after owner and integrity checks.

        Args:
            reference_id: 上传资料引用 ID。
            user_id: 当前用户 ID。
        """
        item = await self.session.scalar(
            select(ConversationResourceReference).where(
                ConversationResourceReference.id == reference_id,
                ConversationResourceReference.user_id == user_id,
                ConversationResourceReference.resource_kind == "uploaded_file",
                ConversationResourceReference.provider == "managed_upload",
                ConversationResourceReference.status == "attached",
            )
        )
        if item is None:
            raise ResourceReferenceError("resource_reference_not_found", 404)
        await ConversationService(self.session).get_owned(
            conversation_id=item.conversation_id,
            user_id=user_id,
        )
        await self._resolve_selected_item(item)
        source = await self._workspace_source(item)
        media_type = guess_type(item.display_name)[0] or "application/octet-stream"
        return UploadedResourceDownload(
            path=source.source_path,
            filename=item.display_name,
            media_type=media_type,
        )

    async def resolve_for_workspace(
        self,
        *,
        task: Task,
        reference_ids: tuple[str, ...],
        expected_versions: dict[str, str],
    ) -> tuple[WorkspaceResourceReference, ...]:
        """Re-authorize the exact Task snapshot selection before reading bytes.

        Args:
            task: 需要处理的任务对象。
            reference_ids: 用于执行当前操作的 reference ids 参数。
            expected_versions: 用于执行当前操作的 expected versions 参数。
        """
        if task.conversation_id is None:
            raise ResourceReferenceError("resource_conversation_required", 422)
        await ConversationService(self.session).get_owned(
            conversation_id=task.conversation_id,
            user_id=task.user_id,
            active_only=True,
        )
        normalized_ids = tuple(
            dict.fromkeys(reference_id.strip() for reference_id in reference_ids)
        )
        if (
            normalized_ids != reference_ids
            or len(normalized_ids) > _MAX_REFERENCE_IDS
            or tuple(expected_versions) != normalized_ids
            or any(
                not version or len(version) > 512
                for version in expected_versions.values()
            )
        ):
            raise ResourceReferenceError("resource_snapshot_invalid", 409)
        resolved: list[WorkspaceResourceReference] = []
        for reference_id in normalized_ids:
            item = await self.session.get(ConversationResourceReference, reference_id)
            if item is None or (
                item.conversation_id != task.conversation_id
                or item.user_id != task.user_id
                or item.tenant_id != task.tenant_id
                or item.organization_id != task.organization_id
                or item.owner_type != task.owner_type
                or item.owner_id != (task.owner_id or task.user_id)
                or item.visibility != task.visibility
            ):
                raise ResourceReferenceError("resource_reference_not_found", 404)
            if item.status != "attached":
                raise ResourceReferenceError("resource_reference_unavailable", 409)
            source = await self._workspace_source(item)
            if source.version != item.version or source.version != expected_versions[reference_id]:
                raise ResourceReferenceError("resource_reference_stale", 409)
            item.last_resolved_at = utc_now()
            resolved.append(
                WorkspaceResourceReference(
                    id=item.id,
                    display_name=item.display_name,
                    source_path=source.source_path,
                    version=source.version,
                    device=source.device,
                    inode=source.inode,
                    size_bytes=source.size_bytes,
                    modified_ns=source.modified_ns,
                )
            )
        await self.session.flush()
        return tuple(resolved)

    async def _resolve_selected_item(self, item: ConversationResourceReference) -> None:
        """Re-authorize a selected kind before its version reaches a Task snapshot.

        Args:
            item: 用于执行当前操作的 item 参数。
        """
        if item.resource_kind == "local_file" and item.provider == "local":
            try:
                local_metadata = _local_file_metadata(item.source_ref)
            except ResourceReferenceError as exc:
                raise ResourceReferenceError("resource_reference_stale", 409) from exc
            if (
                local_metadata.locator_hash != item.source_locator_hash
                or local_metadata.version != item.version
            ):
                raise ResourceReferenceError("resource_reference_stale", 409)
            return
        if item.resource_kind == "uploaded_file" and item.provider == "managed_upload":
            managed_metadata = _managed_upload_metadata(
                self.uploaded_resources_root,
                item.source_ref,
            )
            if (
                managed_metadata.content_hash != item.content_hash
                or managed_metadata.version != item.version
                or managed_metadata.size_bytes != item.size_bytes
            ):
                raise ResourceReferenceError("resource_reference_stale", 409)
            return
        if item.resource_kind == "artifact" and item.provider == "artifact":
            try:
                artifact = await ArtifactLifecycleService(
                    self.session,
                    store=ArtifactStore(self.artifacts_root),
                    auto_commit=False,
                ).resolve_context_input(
                    artifact_id=item.artifact_id or item.source_ref,
                    actor_user_id=item.user_id,
                    conversation_id=item.conversation_id,
                )
            except ArtifactLifecycleError as exc:
                raise ResourceReferenceError("resource_reference_unavailable", 409) from exc
            if (
                artifact.version != item.version
                or artifact.content_hash != item.content_hash
                or artifact.size_bytes != item.size_bytes
            ):
                raise ResourceReferenceError("resource_reference_stale", 409)
            return
        if item.resource_kind == "external_record":
            resolver = self.external_record_resolvers.get(item.provider)
            if resolver is None:
                raise ResourceReferenceError("resource_reference_unavailable", 409)
            try:
                resolution = await resolver.resolve(
                    provider=item.provider,
                    external_resource_id=item.source_ref,
                    user_id=item.user_id,
                    conversation_id=item.conversation_id,
                )
            except Exception as exc:
                raise ResourceReferenceError("resource_reference_unavailable", 409) from exc
            if not resolution.version or resolution.version != item.version:
                raise ResourceReferenceError("resource_reference_stale", 409)
            return
        raise ResourceReferenceError("resource_reference_unsupported", 422)

    async def _workspace_source(
        self, item: ConversationResourceReference
    ) -> WorkspaceResourceReference:
        """Resolve only byte-backed resources into trusted workspace materialization input.

        Args:
            item: 用于执行当前操作的 item 参数。
        """
        if item.resource_kind == "local_file" and item.provider == "local":
            try:
                local_metadata = _local_file_metadata(item.source_ref)
            except ResourceReferenceError as exc:
                raise ResourceReferenceError("resource_reference_stale", 409) from exc
            if local_metadata.locator_hash != item.source_locator_hash:
                raise ResourceReferenceError("resource_reference_stale", 409)
            return WorkspaceResourceReference(
                id=item.id,
                display_name=item.display_name,
                source_path=Path(local_metadata.source_ref),
                version=local_metadata.version,
                device=local_metadata.device,
                inode=local_metadata.inode,
                size_bytes=local_metadata.size_bytes,
                modified_ns=local_metadata.modified_ns,
            )
        if item.resource_kind == "uploaded_file" and item.provider == "managed_upload":
            managed_metadata = _managed_upload_metadata(
                self.uploaded_resources_root,
                item.source_ref,
            )
            return WorkspaceResourceReference(
                id=item.id,
                display_name=item.display_name,
                source_path=managed_metadata.source_path,
                version=managed_metadata.version,
                device=managed_metadata.device,
                inode=managed_metadata.inode,
                size_bytes=managed_metadata.size_bytes,
                modified_ns=managed_metadata.modified_ns,
            )
        if item.resource_kind == "artifact" and item.provider == "artifact":
            try:
                artifact = await ArtifactLifecycleService(
                    self.session,
                    store=ArtifactStore(self.artifacts_root),
                    auto_commit=False,
                ).resolve_context_input(
                    artifact_id=item.artifact_id or item.source_ref,
                    actor_user_id=item.user_id,
                    conversation_id=item.conversation_id,
                )
                stat = artifact.path.stat()
            except (ArtifactLifecycleError, OSError) as exc:
                raise ResourceReferenceError("resource_reference_unavailable", 409) from exc
            return WorkspaceResourceReference(
                id=item.id,
                display_name=item.display_name,
                source_path=artifact.path,
                version=artifact.version,
                device=stat.st_dev,
                inode=stat.st_ino,
                size_bytes=stat.st_size,
                modified_ns=stat.st_mtime_ns,
            )
        raise ResourceReferenceError("resource_reference_unsupported", 422)

    @staticmethod
    def _view(item: ConversationResourceReference) -> ResourceReferenceView:
        """Project persistence into a path-free bounded API/application value.

        Args:
            item: 用于执行当前操作的 item 参数。
        """
        return ResourceReferenceView(
            id=item.id,
            conversation_id=item.conversation_id,
            resource_kind=item.resource_kind,
            provider=item.provider,
            display_name=item.display_name,
            version=item.version,
            content_hash=item.content_hash,
            size_bytes=item.size_bytes,
            user_id=item.user_id,
            tenant_id=item.tenant_id,
            organization_id=item.organization_id,
            owner_type=item.owner_type,
            owner_id=item.owner_id,
            visibility=item.visibility,
            sensitivity=item.sensitivity,
            status=item.status,
            pinned=item.pinned,
            created_at=item.created_at,
            updated_at=item.updated_at,
            last_resolved_at=item.last_resolved_at,
        )


def _local_file_metadata(source_ref: str) -> _LocalFileMetadata:
    """Resolve bounded regular-file metadata without opening or reading content.

    Args:
        source_ref: 用于执行当前操作的 source ref 参数。
    """
    normalized_source = source_ref.strip()
    if not normalized_source or len(source_ref) > _MAX_SOURCE_REF_LENGTH:
        raise ResourceReferenceError("resource_path_invalid", 422)
    try:
        candidate = Path(normalized_source).expanduser()
        if candidate.is_symlink():
            raise ResourceReferenceError("resource_path_invalid", 422)
        resolved = candidate.resolve(strict=True)
        stat = resolved.stat()
    except ResourceReferenceError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResourceReferenceError("resource_path_invalid", 422) from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise ResourceReferenceError("resource_path_invalid", 422)
    identity = f"{resolved}:{stat.st_dev}:{stat.st_ino}"
    version_source = f"{identity}:{stat.st_size}:{stat.st_mtime_ns}"
    return _LocalFileMetadata(
        source_ref=str(resolved),
        locator_hash=sha256(identity.encode("utf-8")).hexdigest(),
        display_name=resolved.name[:255],
        version=sha256(version_source.encode("utf-8")).hexdigest(),
        size_bytes=stat.st_size,
        device=stat.st_dev,
        inode=stat.st_ino,
        modified_ns=stat.st_mtime_ns,
    )


def _normalize_sensitivity(sensitivity: str) -> str:
    """Normalize the bounded sensitivity values shared by all resource kinds.

    Args:
        sensitivity: 用于执行当前操作的 sensitivity 参数。
    """
    safe_sensitivity = sensitivity.strip().lower()
    if safe_sensitivity not in {"normal", "sensitive", "restricted"}:
        raise ResourceReferenceError("resource_sensitivity_invalid", 422)
    return safe_sensitivity


def _normalize_uploaded_filename(filename: str) -> str:
    """Reject paths and control characters before retaining an upload display name.

    Args:
        filename: 用于执行当前操作的 filename 参数。
    """
    safe_filename = filename.strip()
    if (
        not safe_filename
        or len(safe_filename) > 255
        or Path(safe_filename).name != safe_filename
        or "/" in safe_filename
        or "\\" in safe_filename
        or "\x00" in safe_filename
    ):
        raise ResourceReferenceError("resource_upload_invalid", 422)
    return safe_filename


def _normalize_external_provider(provider: str) -> str:
    """Accept a short stable provider key without URLs or whitespace.

    Args:
        provider: 用于执行当前操作的 provider 参数。
    """
    safe_provider = provider.strip().lower()
    if (
        not safe_provider
        or len(safe_provider) > _MAX_EXTERNAL_PROVIDER_LENGTH
        or not all(character.isalnum() or character in {"-", "_", "."} for character in safe_provider)
    ):
        raise ResourceReferenceError("resource_external_invalid", 422)
    return safe_provider


def _normalize_external_value(value: str, maximum_length: int) -> str:
    """Normalize opaque bounded external fields without parsing their contents.

    Args:
        value: 待校验、归一化或转换的输入值。
        maximum_length: 用于执行当前操作的 maximum length 参数。
    """
    safe_value = value.strip()
    if (
        not safe_value
        or len(safe_value) > maximum_length
        or "\x00" in safe_value
        or any(character in "\r\n" for character in safe_value)
    ):
        raise ResourceReferenceError("resource_external_invalid", 422)
    return safe_value


def _normalize_display_name(display_name: str) -> str:
    """Retain a bounded user-facing label without source or provider semantics.

    Args:
        display_name: 用于执行当前操作的 display name 参数。
    """
    safe_name = display_name.strip()
    if (
        not safe_name
        or len(safe_name) > 255
        or "\x00" in safe_name
        or any(character in "\r\n" for character in safe_name)
    ):
        raise ResourceReferenceError("resource_external_invalid", 422)
    return safe_name


def _managed_upload_path(root: Path, storage_ref: str) -> Path:
    """Resolve one opaque upload key under its fixed backend-owned root.

    Args:
        root: 用于执行当前操作的 root 参数。
        storage_ref: 用于执行当前操作的 storage ref 参数。
    """
    if (
        not storage_ref
        or len(storage_ref) > 64
        or Path(storage_ref).name != storage_ref
        or not all(character in "0123456789abcdef" for character in storage_ref)
    ):
        raise ResourceReferenceError("resource_reference_stale", 409)
    return root / storage_ref[:2] / storage_ref


def _managed_upload_metadata(root: Path, storage_ref: str) -> _ManagedUploadMetadata:
    """Hash and stat only a bounded managed upload when resolving task context.

    Args:
        root: 用于执行当前操作的 root 参数。
        storage_ref: 用于执行当前操作的 storage ref 参数。
    """
    try:
        source_path = _managed_upload_path(root, storage_ref)
        if source_path.is_symlink() or not source_path.is_file():
            raise ResourceReferenceError("resource_reference_stale", 409)
        stat = source_path.stat()
        if stat.st_size <= 0 or stat.st_size > _MAX_UPLOADED_FILE_BYTES:
            raise ResourceReferenceError("resource_reference_stale", 409)
        content_hash = sha256(source_path.read_bytes()).hexdigest()
    except ResourceReferenceError:
        raise
    except OSError as exc:
        raise ResourceReferenceError("resource_reference_stale", 409) from exc
    return _ManagedUploadMetadata(
        source_path=source_path,
        content_hash=content_hash,
        version=content_hash,
        size_bytes=stat.st_size,
        device=stat.st_dev,
        inode=stat.st_ino,
        modified_ns=stat.st_mtime_ns,
    )


__all__ = [
    "DEFAULT_EXTERNAL_RECORD_RESOLVERS",
    "ExternalRecordResolution",
    "ExternalRecordResolver",
    "ExternalRecordResolverRegistry",
    "ResolvedResourceReference",
    "ResourceReferenceError",
    "ResourceReferenceService",
    "ResourceReferenceView",
    "UploadedResourceDownload",
    "WorkspaceResourceReference",
]
