from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.memory.semantic import SemanticMemory
from domain.models import Memory, MemoryIndexOutbox, utc_now


@dataclass(frozen=True)
class MemoryIndexOutboxResult:
    """表示 处理 memory index outbox result 的后端数据结构或服务对象。"""

    recovered_count: int = 0
    processed_count: int = 0
    succeeded_count: int = 0
    retry_count: int = 0
    failed_count: int = 0


@dataclass(frozen=True)
class _OperationResult:
    """表示 处理 operation result 的后端数据结构或服务对象。"""

    succeeded: bool
    error_code: str | None = None
    retryable: bool = True


class MemoryIndexOutboxConsumer:
    """表示 处理 memory index outbox consumer 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        semantic_memory: SemanticMemory,
        batch_size: int = 20,
        max_attempts: int = 3,
        lease_timeout: timedelta = timedelta(minutes=10),
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            semantic_memory: semantic_memory 参数。
            batch_size: batch_size 参数。
            max_attempts: max_attempts 参数。
            lease_timeout: lease_timeout 参数。
        """
        self.session = session
        self.semantic_memory = semantic_memory
        self.batch_size = max(1, min(batch_size, 100))
        self.max_attempts = max(1, max_attempts)
        self.lease_timeout = lease_timeout

    async def run_once(self, *, now: datetime | None = None) -> MemoryIndexOutboxResult:
        """运行 once。

        Args:
            now: now 参数。
        """
        current = now or utc_now()
        recovered = await self._recover_stale(current)
        items = list(
            await self.session.scalars(
                select(MemoryIndexOutbox)
                .where(MemoryIndexOutbox.status.in_(("pending", "retry")))
                .order_by(
                    MemoryIndexOutbox.created_at.asc(), MemoryIndexOutbox.id.asc()
                )
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        succeeded = retries = failed = 0
        for item in items:
            item_id = item.id
            item.status = "processing"
            item.attempts += 1
            item.last_error_code = None
            await self.session.commit()

            operation_result = await self._apply(item_id)
            stored = await self.session.get(MemoryIndexOutbox, item_id)
            if stored is None:
                continue
            if operation_result.succeeded:
                await self._remove_duplicate_terminal(stored, "succeeded")
                stored.status = "succeeded"
                stored.last_error_code = None
                succeeded += 1
            elif operation_result.retryable and stored.attempts < self.max_attempts:
                stored.status = "retry"
                stored.last_error_code = operation_result.error_code
                retries += 1
            else:
                await self._remove_duplicate_terminal(stored, "failed")
                stored.status = "failed"
                stored.last_error_code = operation_result.error_code
                failed += 1
            await self.session.commit()

        return MemoryIndexOutboxResult(
            recovered_count=recovered,
            processed_count=len(items),
            succeeded_count=succeeded,
            retry_count=retries,
            failed_count=failed,
        )

    async def _remove_duplicate_terminal(
        self, item: MemoryIndexOutbox, target_status: str
    ) -> None:
        """执行 移除 duplicate terminal 的内部辅助逻辑。

        Args:
            item: item 参数。
            target_status: target_status 参数。
        """
        duplicates = list(
            await self.session.scalars(
                select(MemoryIndexOutbox).where(
                    MemoryIndexOutbox.memory_id == item.memory_id,
                    MemoryIndexOutbox.operation == item.operation,
                    MemoryIndexOutbox.status == target_status,
                    MemoryIndexOutbox.id != item.id,
                )
            )
        )
        for duplicate in duplicates:
            await self.session.delete(duplicate)

    async def _recover_stale(self, now: datetime) -> int:
        """执行 处理 recover stale 的内部辅助逻辑。

        Args:
            now: now 参数。
        """
        stale = list(
            await self.session.scalars(
                select(MemoryIndexOutbox).where(
                    MemoryIndexOutbox.status == "processing",
                    MemoryIndexOutbox.updated_at < now - self.lease_timeout,
                )
            )
        )
        for item in stale:
            item.status = "retry"
            item.last_error_code = "processing_lease_expired"
        if stale:
            await self.session.commit()
        return len(stale)

    async def _apply(self, item_id: str) -> _OperationResult:
        """执行 处理 apply 的内部辅助逻辑。

        Args:
            item_id: item_id 参数。
        """
        item = await self.session.get(MemoryIndexOutbox, item_id)
        if item is None:
            return _OperationResult(False, "outbox_missing", retryable=False)
        if not self.semantic_memory.enabled:
            return _OperationResult(False, "semantic_memory_disabled", retryable=False)

        try:
            if item.operation == "delete":
                succeeded = await self.semantic_memory.delete(
                    user_id=item.user_id,
                    memory_id=item.memory_id,
                )
                return _OperationResult(
                    succeeded, None if succeeded else "semantic_delete_failed"
                )

            memory = await self.session.get(Memory, item.memory_id)
            if (
                memory is None
                or memory.user_id != item.user_id
                or memory.status != "active"
            ):
                return _OperationResult(False, "memory_not_active", retryable=False)
            if item.operation == "rebuild":
                deleted = await self.semantic_memory.delete(
                    user_id=item.user_id,
                    memory_id=item.memory_id,
                )
                if not deleted:
                    return _OperationResult(False, "semantic_delete_failed")
            if item.operation in {"add", "rebuild"}:
                added = await self.semantic_memory.add(
                    user_id=item.user_id,
                    run_id=f"memory-index-outbox:{item.id}",
                    memory_id=item.memory_id,
                    content=memory.content,
                )
                return _OperationResult(added, None if added else "semantic_add_failed")
            return _OperationResult(
                False, "unsupported_index_operation", retryable=False
            )
        except Exception:
            return _OperationResult(False, "semantic_index_exception")
