from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeAlias

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from .client import DesktopApiClient
from .worker import ApiWorker


ClientFactory: TypeAlias = Callable[..., DesktopApiClient]
SuccessHandler: TypeAlias = Callable[[Any], None]


class ReminderManagerDialog(QDialog):
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
        self.setWindowTitle("提醒管理")
        self.resize(540, 520)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("我的提醒"))
        header.addStretch()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_reminders)
        header.addWidget(refresh)
        layout.addLayout(header)
        self.reminder_list = QListWidget()
        self.reminder_list.setObjectName("reminder_list")
        layout.addWidget(self.reminder_list)

        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setObjectName("reminder_title")
        self.message = QLineEdit()
        self.message.setObjectName("reminder_message")
        self.due_at = QLineEdit()
        self.due_at.setObjectName("reminder_due_at")
        self.due_at.setPlaceholderText("2026-07-15T09:00:00+08:00")
        self.channel = QComboBox()
        self.channel.setObjectName("reminder_channel")
        self.channel.addItem("桌面通知", "desktop")
        self.channel.addItem("LangBot", "langbot")
        form.addRow("标题", self.title)
        form.addRow("内容", self.message)
        form.addRow("时间（ISO 8601）", self.due_at)
        form.addRow("渠道", self.channel)
        layout.addLayout(form)

        actions = QHBoxLayout()
        create = QPushButton("创建提醒")
        create.setObjectName("create_reminder")
        create.clicked.connect(self.create_reminder)
        cancel = QPushButton("取消所选")
        cancel.setObjectName("cancel_reminder")
        cancel.clicked.connect(self.cancel_selected)
        actions.addWidget(create)
        actions.addWidget(cancel)
        layout.addLayout(actions)
        self.status_label = QLabel("创建时间必须包含时区。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def create_reminder(self) -> None:
        title = self.title.text().strip()
        message = self.message.text().strip()
        due_at = self.due_at.text().strip()
        try:
            parsed = datetime.fromisoformat(due_at)
        except ValueError:
            parsed = None
        if not title or not message or parsed is None or parsed.tzinfo is None:
            self.status_label.setText("请完整填写标题、内容和带时区的 ISO 8601 时间。")
            return
        self._start_request(
            "mutate",
            lambda: self._with_client(
                lambda client: client.create_reminder(
                    title=title,
                    message=message,
                    due_at=due_at,
                    channel=str(self.channel.currentData()),
                )
            ),
            lambda value: self._mutation_succeeded(value, "提醒已创建。"),
        )

    def refresh_reminders(self) -> None:
        self._start_request(
            "list",
            lambda: self._with_client(lambda client: client.list_reminders()),
            self._reminders_refreshed,
        )

    def cancel_selected(self) -> None:
        item = self.reminder_list.currentItem()
        reminder = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(reminder, dict):
            self.status_label.setText("请先选择一个提醒。")
            return
        reminder_id = str(reminder.get("reminder_id") or "")
        self._start_request(
            "mutate",
            lambda: self._with_client(
                lambda client: client.cancel_reminder(reminder_id)
            ),
            lambda value: self._mutation_succeeded(value, "提醒已取消。"),
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

    def _reminders_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("提醒列表响应无效。")
            return
        self.reminder_list.clear()
        for reminder in value:
            if not isinstance(reminder, dict):
                continue
            state = reminder.get("delivery_status") or reminder.get("status", "")
            error = reminder.get("last_error_code")
            suffix = f" · {error}" if error else ""
            item = QListWidgetItem(
                f"[{state}] {reminder.get('title', '')} · "
                f"{reminder.get('due_at', '')} · {reminder.get('channel', '')}"
                f"{suffix}"
            )
            item.setData(Qt.ItemDataRole.UserRole, reminder)
            self.reminder_list.addItem(item)
        if self.reminder_list.count():
            self.reminder_list.setCurrentRow(0)
        self.status_label.setText(f"已加载 {self.reminder_list.count()} 个提醒。")

    def _mutation_succeeded(self, value: object, message: str) -> None:
        del value
        self.status_label.setText(message)
        self.refresh_reminders()
