from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .client import DesktopApiClient
from .worker import ApiWorker


ClientFactory: TypeAlias = Callable[..., DesktopApiClient]
SuccessHandler: TypeAlias = Callable[[Any], None]
_REASON_LABELS = {
    "explicit_user_request": "你明确要求保存",
    "conflict_detected": "与已有记忆冲突，等待确认",
    "content_hash_match": "与已有记忆内容相同",
    "user_requested_rebuild": "你请求重建语义索引",
}


class MemoryCenterDialog(QDialog):
    page_size = 50

    def __init__(
        self,
        *,
        base_url: str,
        user_id: str,
        parent: object | None = None,
        thread_pool: QThreadPool | None = None,
        client_factory: ClientFactory = DesktopApiClient,
        api_token: str = "",
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.base_url = base_url
        self.user_id = user_id
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.client_factory = client_factory
        self.api_token = api_token
        self._workers: set[ApiWorker] = set()
        self._busy_operations: set[str] = set()
        self._offset = 0
        self._current_memory_id: str | None = None
        self.setWindowTitle("Memory Center")
        self.setAccessibleName("Memory Center 记忆管理中心")
        self.resize(820, 680)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("记忆管理中心"))
        header.addStretch()
        refresh = QPushButton("刷新全部")
        refresh.setObjectName("refresh_memory_center")
        refresh.setAccessibleName("刷新记忆中心")
        refresh.clicked.connect(self.refresh_all)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("memory_center_tabs")
        self._build_overview_tab()
        self._build_list_tab()
        self._build_detail_tab()
        self._build_state_tab("候选", "candidate", "memory_candidates")
        self._build_state_tab("冲突", "conflict_pending", "memory_conflicts")
        self._build_retrieval_tab()
        self._build_settings_tab()
        layout.addWidget(self.tabs)

        self.status_label = QLabel("点击刷新加载记忆。")
        self.status_label.setObjectName("memory_center_status")
        self.status_label.setAccessibleName("记忆中心状态")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _build_overview_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("memory_overview_tab")
        layout = QVBoxLayout(tab)
        self.overview_label = QLabel("尚未加载")
        self.overview_label.setObjectName("memory_overview")
        self.overview_label.setWordWrap(True)
        layout.addWidget(self.overview_label)

        form = QFormLayout()
        self.remember_content = QLineEdit()
        self.remember_content.setObjectName("memory_remember_content")
        self.remember_content.setPlaceholderText("输入希望长期记住的内容")
        self.remember_type = self._combo(
            "memory_remember_type",
            (("偏好", "preference"), ("事实", "fact"), ("约束", "constraint")),
        )
        remember = QPushButton("记住")
        remember.setObjectName("memory_remember")
        remember.setAccessibleName("保存新记忆")
        remember.clicked.connect(self.remember)
        form.addRow("内容", self.remember_content)
        form.addRow("类型", self.remember_type)
        form.addRow("", remember)
        layout.addLayout(form)
        layout.addStretch()
        self.tabs.addTab(tab, "Overview")

    def _build_list_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("memory_list_tab")
        layout = QVBoxLayout(tab)
        filters = QHBoxLayout()
        self.status_filter = self._filter_combo(
            "memory_status_filter",
            ("active", "candidate", "conflict_pending", "superseded", "archived"),
        )
        self.type_filter = self._filter_combo(
            "memory_type_filter",
            ("episode", "fact", "preference", "constraint", "procedure", "reflection"),
        )
        self.scope_filter = self._filter_combo(
            "memory_scope_filter",
            ("user/global", "user/project", "user/conversation", "agent/profile"),
        )
        self.sensitivity_filter = self._filter_combo(
            "memory_sensitivity_filter", ("public", "sensitive")
        )
        for combo in (
            self.status_filter,
            self.type_filter,
            self.scope_filter,
            self.sensitivity_filter,
        ):
            filters.addWidget(combo)
        apply_filters = QPushButton("筛选")
        apply_filters.setObjectName("filter_memories")
        apply_filters.clicked.connect(self._reset_and_refresh_memories)
        filters.addWidget(apply_filters)
        layout.addLayout(filters)

        self.memory_list = QListWidget()
        self.memory_list.setObjectName("memory_list")
        self.memory_list.setAccessibleName("记忆列表")
        self.memory_list.currentItemChanged.connect(self._memory_selected)
        layout.addWidget(self.memory_list)

        pagination = QHBoxLayout()
        previous = QPushButton("上一页")
        previous.setObjectName("previous_memory_page")
        previous.clicked.connect(self.previous_page)
        next_page = QPushButton("下一页")
        next_page.setObjectName("next_memory_page")
        next_page.clicked.connect(self.next_page)
        self.page_label = QLabel("第 1 页")
        self.page_label.setObjectName("memory_page")
        pagination.addWidget(previous)
        pagination.addWidget(next_page)
        pagination.addStretch()
        pagination.addWidget(self.page_label)
        layout.addLayout(pagination)
        self.tabs.addTab(tab, "List")

    def _build_detail_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("memory_detail_tab")
        layout = QVBoxLayout(tab)
        self.detail_text = QPlainTextEdit()
        self.detail_text.setObjectName("memory_detail")
        self.detail_text.setAccessibleName("记忆详情")
        self.detail_text.setReadOnly(True)
        layout.addWidget(self.detail_text)

        actions = QHBoxLayout()
        for label, action in (
            ("确认", "confirm"),
            ("拒绝", "reject"),
            ("Pin", "pin"),
            ("取消 Pin", "unpin"),
            ("归档", "archive"),
            ("忘记", "forget"),
            ("重建索引", "rebuild-index"),
        ):
            button = QPushButton(label)
            button.setObjectName(f"memory_action_{action}")
            button.setAccessibleName(f"记忆操作：{label}")
            button.clicked.connect(lambda _checked=False, value=action: self.perform_action(value))
            actions.addWidget(button)
        layout.addLayout(actions)

        correct_row = QHBoxLayout()
        self.corrected_content = QLineEdit()
        self.corrected_content.setObjectName("memory_corrected_content")
        self.corrected_content.setPlaceholderText("输入纠正后的完整内容")
        correct = QPushButton("纠正并确认")
        correct.setObjectName("memory_action_correct")
        correct.clicked.connect(self.correct)
        correct_row.addWidget(self.corrected_content)
        correct_row.addWidget(correct)
        layout.addLayout(correct_row)

        scope_row = QHBoxLayout()
        self.scope_kind = self._combo(
            "memory_scope_kind",
            (
                ("全局", "user/global"),
                ("项目", "user/project"),
                ("会话", "user/conversation"),
                ("Agent Profile", "agent/profile"),
            ),
        )
        self.scope_id = QLineEdit()
        self.scope_id.setObjectName("memory_scope_id")
        self.scope_id.setPlaceholderText("非全局作用域需要 ID")
        scope = QPushButton("更新作用域")
        scope.setObjectName("memory_action_scope")
        scope.clicked.connect(self.change_scope)
        scope_row.addWidget(self.scope_kind)
        scope_row.addWidget(self.scope_id)
        scope_row.addWidget(scope)
        layout.addLayout(scope_row)

        validity_row = QHBoxLayout()
        self.valid_from = QLineEdit()
        self.valid_from.setObjectName("memory_valid_from")
        self.valid_from.setPlaceholderText("valid_from ISO，可留空")
        self.valid_to = QLineEdit()
        self.valid_to.setObjectName("memory_valid_to")
        self.valid_to.setPlaceholderText("valid_to ISO，可留空")
        validity = QPushButton("更新有效期")
        validity.setObjectName("memory_action_validity")
        validity.clicked.connect(self.change_validity)
        validity_row.addWidget(self.valid_from)
        validity_row.addWidget(self.valid_to)
        validity_row.addWidget(validity)
        layout.addLayout(validity_row)
        self.tabs.addTab(tab, "Detail")

    def _build_state_tab(self, label: str, status: str, object_name: str) -> None:
        tab = QWidget()
        tab.setObjectName(f"{object_name}_tab")
        layout = QVBoxLayout(tab)
        widget = QListWidget()
        widget.setObjectName(object_name)
        widget.setAccessibleName(f"记忆{label}列表")
        widget.currentItemChanged.connect(self._memory_selected)
        layout.addWidget(widget)
        if status == "candidate":
            self.candidate_list = widget
        else:
            self.conflict_list = widget
        self.tabs.addTab(tab, label)

    def _build_retrieval_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("memory_retrieval_tab")
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.retrieval_task_id = QLineEdit()
        self.retrieval_task_id.setObjectName("memory_retrieval_task_id")
        self.retrieval_task_id.setPlaceholderText("输入本人 task_id")
        inspect = QPushButton("查看为何使用")
        inspect.setObjectName("inspect_memory_retrieval")
        inspect.clicked.connect(self.inspect_retrieval)
        row.addWidget(self.retrieval_task_id)
        row.addWidget(inspect)
        layout.addLayout(row)
        self.retrieval_text = QPlainTextEdit()
        self.retrieval_text.setObjectName("memory_retrieval_detail")
        self.retrieval_text.setReadOnly(True)
        layout.addWidget(self.retrieval_text)
        self.tabs.addTab(tab, "Retrieval")

    def _build_settings_tab(self) -> None:
        tab = QWidget()
        tab.setObjectName("memory_settings_tab")
        layout = QVBoxLayout(tab)
        policy_row = QHBoxLayout()
        self.policy_type = self._combo(
            "memory_policy_type",
            tuple((value, value) for value in (
                "episode", "fact", "preference", "constraint", "procedure", "reflection"
            )),
        )
        self.policy_enabled = QCheckBox("不再自动记住此类型")
        self.policy_enabled.setObjectName("memory_policy_enabled")
        save_policy = QPushButton("保存策略")
        save_policy.setObjectName("save_memory_policy")
        save_policy.clicked.connect(self.save_policy)
        policy_row.addWidget(self.policy_type)
        policy_row.addWidget(self.policy_enabled)
        policy_row.addWidget(save_policy)
        layout.addLayout(policy_row)
        layout.addWidget(QLabel("当前策略"))
        self.policy_list = QListWidget()
        self.policy_list.setObjectName("memory_policies")
        layout.addWidget(self.policy_list)
        layout.addWidget(QLabel("最近 consolidation digest"))
        self.digest_list = QListWidget()
        self.digest_list.setObjectName("memory_digests")
        layout.addWidget(self.digest_list)
        self.tabs.addTab(tab, "Settings")

    @staticmethod
    def _combo(name: str, items: tuple[tuple[str, str], ...]) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(name)
        for label, value in items:
            combo.addItem(label, value)
        return combo

    @classmethod
    def _filter_combo(cls, name: str, values: tuple[str, ...]) -> QComboBox:
        return cls._combo(name, (("全部", ""), *( (value, value) for value in values )))

    def refresh_all(self) -> None:
        self.refresh_overview()
        self.refresh_memories()
        self.refresh_state_list("candidate", self.candidate_list)
        self.refresh_state_list("conflict_pending", self.conflict_list)
        self.refresh_settings()

    def refresh_overview(self) -> None:
        self._start_request(
            "overview",
            lambda: self._with_client(lambda client: client.get_memory_overview()),
            self._overview_refreshed,
        )

    def refresh_memories(self) -> None:
        self._start_request(
            "memories",
            lambda: self._with_client(
                lambda client: client.list_memories(
                    status=self._selected(self.status_filter),
                    memory_type=self._selected(self.type_filter),
                    scope_kind=self._selected(self.scope_filter),
                    sensitivity=self._selected(self.sensitivity_filter),
                    limit=self.page_size,
                    offset=self._offset,
                )
            ),
            self._memories_refreshed,
        )

    def refresh_state_list(self, status: str, widget: QListWidget) -> None:
        self._start_request(
            f"state-{status}",
            lambda: self._with_client(
                lambda client: client.list_memories(status=status, limit=self.page_size)
            ),
            lambda value: self._state_refreshed(value, widget, status),
        )

    def refresh_settings(self) -> None:
        self._start_request(
            "policies",
            lambda: self._with_client(lambda client: client.list_memory_policies()),
            self._policies_refreshed,
        )
        self._start_request(
            "digests",
            lambda: self._with_client(lambda client: client.list_memory_digests()),
            self._digests_refreshed,
        )

    def remember(self) -> None:
        content = self.remember_content.text().strip()
        if not content:
            self.status_label.setText("请输入要记住的内容。")
            return
        self._start_request(
            "remember",
            lambda: self._with_client(
                lambda client: client.create_memory(
                    content=content,
                    memory_type=str(self.remember_type.currentData()),
                )
            ),
            self._action_succeeded,
        )

    def perform_action(self, action: str, **values: object) -> None:
        if self._current_memory_id is None:
            self.status_label.setText("请先选择一条记忆。")
            return
        memory_id = self._current_memory_id
        self.status_label.setText("正在提交记忆操作…")
        self._start_request(
            "memory-action",
            lambda: self._with_client(
                lambda client: client.perform_memory_action(memory_id, action, **values)
            ),
            self._action_succeeded,
        )

    def correct(self) -> None:
        content = self.corrected_content.text().strip()
        if not content:
            self.status_label.setText("请输入纠正后的完整内容。")
            return
        self.perform_action("correct", content=content)

    def change_scope(self) -> None:
        self.perform_action(
            "scope",
            scope_kind=str(self.scope_kind.currentData()),
            scope_id=self.scope_id.text().strip() or None,
        )

    def change_validity(self) -> None:
        self.perform_action(
            "validity",
            valid_from=self.valid_from.text().strip() or None,
            valid_to=self.valid_to.text().strip() or None,
        )

    def inspect_retrieval(self) -> None:
        task_id = self.retrieval_task_id.text().strip()
        if not task_id:
            self.status_label.setText("请输入本人 task_id。")
            return
        self._start_request(
            "retrieval",
            lambda: self._with_client(
                lambda client: client.get_task_memory_retrieval(task_id)
            ),
            self._retrieval_refreshed,
        )

    def save_policy(self) -> None:
        memory_type = str(self.policy_type.currentData())
        self._start_request(
            "policy-save",
            lambda: self._with_client(
                lambda client: client.update_memory_policy(
                    f"never_remember:{memory_type}",
                    enabled=self.policy_enabled.isChecked(),
                )
            ),
            lambda _value: self._policy_saved(),
        )

    def previous_page(self) -> None:
        self._offset = max(0, self._offset - self.page_size)
        self.refresh_memories()

    def next_page(self) -> None:
        if self.memory_list.count() < self.page_size:
            self.status_label.setText("已到最后一页。")
            return
        self._offset += self.page_size
        self.refresh_memories()

    def _reset_and_refresh_memories(self) -> None:
        self._offset = 0
        self.refresh_memories()

    @staticmethod
    def _selected(combo: QComboBox) -> str | None:
        value = str(combo.currentData() or "")
        return value or None

    def _memory_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        payload = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        memory_id = str(payload.get("memory_id") or "")
        if not memory_id:
            return
        self._current_memory_id = memory_id
        self._load_detail(memory_id)

    def _load_detail(self, memory_id: str) -> None:
        self._start_request(
            "detail",
            lambda: self._with_client(
                lambda client: client.get_memory_detail(memory_id)
            ),
            self._detail_refreshed,
        )

    def _with_client(self, operation: Callable[[DesktopApiClient], Any]) -> Any:
        kwargs = {"base_url": self.base_url, "user_id": self.user_id}
        if self.api_token:
            kwargs["api_token"] = self.api_token
        client = self.client_factory(**kwargs)
        try:
            return operation(client)
        finally:
            client.close()

    def _start_request(
        self, key: str, operation: Callable[[], Any], on_success: SuccessHandler
    ) -> None:
        if key in self._busy_operations:
            return
        self._busy_operations.add(key)
        worker = ApiWorker(operation)
        self._workers.add(worker)
        worker.signals.succeeded.connect(on_success)
        worker.signals.failed.connect(self.status_label.setText)
        worker.signals.finished.connect(lambda: self._request_finished(key, worker))
        self.thread_pool.start(worker)

    def _request_finished(self, key: str, worker: ApiWorker) -> None:
        self._busy_operations.discard(key)
        self._workers.discard(worker)

    def _overview_refreshed(self, value: object) -> None:
        if not isinstance(value, dict):
            self.status_label.setText("Overview 响应无效。")
            return
        counts = value.get("counts")
        if not isinstance(counts, dict):
            counts = {}
        self.overview_label.setText(
            " · ".join(
                (
                    f"Active {counts.get('active', 0)}",
                    f"Candidate {counts.get('candidate', 0)}",
                    f"Conflict {counts.get('conflict_pending', 0)}",
                    f"Index pending {value.get('pending_index_count', 0)}",
                )
            )
        )
        self.status_label.setText("Memory overview 已刷新。")

    def _memories_refreshed(self, value: object) -> None:
        items = self._payload_items(value)
        if items is None:
            self.status_label.setText("记忆列表响应无效。")
            return
        self._fill_memory_list(self.memory_list, items)
        self.page_label.setText(f"第 {self._offset // self.page_size + 1} 页")
        self.status_label.setText(f"已加载 {len(items)} 条记忆。")

    def _state_refreshed(
        self, value: object, widget: QListWidget, status: str
    ) -> None:
        items = self._payload_items(value)
        if items is None:
            self.status_label.setText("候选或冲突列表响应无效。")
            return
        self._fill_memory_list(widget, items)
        self.status_label.setText(f"已加载 {len(items)} 条 {status} 记忆。")

    @staticmethod
    def _payload_items(value: object) -> list[dict[str, Any]] | None:
        if not isinstance(value, dict) or not isinstance(value.get("items"), list):
            return None
        return [item for item in value["items"] if isinstance(item, dict)]

    @staticmethod
    def _fill_memory_list(widget: QListWidget, items: list[dict[str, Any]]) -> None:
        widget.clear()
        for memory in items:
            content = str(memory.get("content") or "")
            item = QListWidgetItem(
                f"[{memory.get('status', '')}] {memory.get('memory_type', '')} · "
                f"{content[:80]}"
            )
            item.setData(Qt.ItemDataRole.UserRole, memory)
            widget.addItem(item)

    def _detail_refreshed(self, value: object) -> None:
        if not isinstance(value, dict) or not isinstance(value.get("memory"), dict):
            self.status_label.setText("记忆详情响应无效。")
            return
        memory = value["memory"]
        reason = str(memory.get("reason_code") or "")
        lines = [
            f"ID: {memory.get('memory_id', '')}",
            f"状态: {memory.get('status', '')}",
            f"类型: {memory.get('memory_type', '')}",
            f"内容: {memory.get('content', '')}",
            f"来源: {memory.get('source_kind', '')}",
            f"原因: {_REASON_LABELS.get(reason, reason or '未提供')}",
            f"作用域: {memory.get('scope_kind', '')} / {memory.get('scope_id') or '-'}",
            f"有效期: {memory.get('valid_from') or '-'} → {memory.get('valid_to') or '-'}",
            f"Links: {len(value.get('links') or [])}",
            f"Feedback: {len(value.get('feedback') or [])}",
            f"Usage: {len(value.get('usage') or [])}",
        ]
        self.detail_text.setPlainText("\n".join(lines))
        detail_tab = self.detail_text.parentWidget()
        if detail_tab is not None:
            self.tabs.setCurrentWidget(detail_tab)
        self.status_label.setText("记忆详情已加载。")

    def _action_succeeded(self, value: object) -> None:
        if not isinstance(value, dict) or not isinstance(value.get("memory"), dict):
            self.status_label.setText("记忆操作响应无效。")
            return
        memory_id = str(value["memory"].get("memory_id") or "")
        if memory_id:
            self._current_memory_id = memory_id
            self._load_detail(memory_id)
        self.remember_content.clear()
        self.corrected_content.clear()
        self.status_label.setText("记忆操作成功，正在刷新服务端状态。")
        self.refresh_overview()
        self.refresh_memories()
        self.refresh_state_list("candidate", self.candidate_list)
        self.refresh_state_list("conflict_pending", self.conflict_list)

    def _retrieval_refreshed(self, value: object) -> None:
        if not isinstance(value, dict):
            self.status_label.setText("Retrieval 响应无效。")
            return
        trace = value.get("trace")
        items = value.get("items")
        if not isinstance(trace, dict) or not isinstance(items, list):
            self.status_label.setText("Retrieval 响应无效。")
            return
        lines = [
            f"模式: {trace.get('retrieval_mode', '')}",
            f"时间意图: {trace.get('time_intent', '')}",
            f"使用数量: {trace.get('injected_count', 0)}",
        ]
        lines.extend(
            f"- {item.get('memory_id', '')} · rank {item.get('final_rank', '-')} · "
            f"{item.get('filter_reason', '')}"
            for item in items
            if isinstance(item, dict)
        )
        self.retrieval_text.setPlainText("\n".join(lines))
        self.status_label.setText("Retrieval 解释已加载。")

    def _policies_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("策略响应无效。")
            return
        self.policy_list.clear()
        for policy in value:
            if isinstance(policy, dict):
                self.policy_list.addItem(
                    f"{policy.get('policy_key', '')}: "
                    f"{'启用' if policy.get('enabled') else '停用'}"
                )

    def _digests_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("Digest 响应无效。")
            return
        self.digest_list.clear()
        for digest in value:
            if isinstance(digest, dict):
                self.digest_list.addItem(
                    f"{digest.get('digest_type', '')} · "
                    f"{digest.get('window_start', '')} → {digest.get('window_end', '')}"
                )

    def _policy_saved(self) -> None:
        self.status_label.setText("记忆策略已保存，正在刷新。")
        self.refresh_settings()
