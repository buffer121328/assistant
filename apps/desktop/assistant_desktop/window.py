from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any, TypeAlias

from PySide6.QtCore import QSettings, QThreadPool, QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .client import (
    ApprovalDecision,
    ApprovalDecisionResult,
    DEFAULT_API_BASE_URL,
    DesktopApiClient,
    SubmissionResult,
    normalize_connection_settings,
)
from .account_dialog import AccountManagerDialog
from .knowledge_dialog import KnowledgeManagerDialog
from .reminder_dialog import ReminderManagerDialog
from .skill_dialog import SkillManagerDialog
from .secure_store import KeyringTokenStore, TokenStore
from .worker import ApiWorker


ClientFactory: TypeAlias = Callable[..., DesktopApiClient]
SuccessHandler: TypeAlias = Callable[[Any], None]


class TaskWindow(QMainWindow):
    def __init__(
        self,
        *,
        settings: QSettings | None = None,
        thread_pool: QThreadPool | None = None,
        client_factory: ClientFactory = DesktopApiClient,
        token_store: TokenStore | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or QSettings("PersonalAgent", "AssistantDesktop")
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.client_factory = client_factory
        self.token_store = token_store or KeyringTokenStore()
        self._workers: set[ApiWorker] = set()
        self._busy_operations: set[str] = set()
        self._current_task_id: str | None = None
        self._skill_dialog: SkillManagerDialog | None = None
        self._account_dialog: AccountManagerDialog | None = None
        self._knowledge_dialog: KnowledgeManagerDialog | None = None
        self._reminder_dialog: ReminderManagerDialog | None = None
        self._quitting = False

        self.setWindowTitle("个人 Agent 助手")
        self.setFixedSize(440, 680)
        self._build_ui()
        self._build_tray()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2000)
        self.refresh_timer.timeout.connect(self.refresh_tasks)
        self.refresh_timer.timeout.connect(self.poll_notifications)
        self.refresh_timer.start()

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel("个人 Agent 助手")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        settings_group = QGroupBox("连接")
        settings_form = QFormLayout(settings_group)
        self.api_url = QLineEdit(
            str(self.settings.value("api_base_url", DEFAULT_API_BASE_URL))
        )
        self.api_url.setObjectName("api_base_url")
        self.user_id = QLineEdit(str(self.settings.value("user_id", "")))
        self.user_id.setObjectName("user_id")
        self.user_id.setPlaceholderText("已有用户 ID")
        self.api_token = QLineEdit()
        self.api_token.setObjectName("api_token")
        self.api_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_token.setPlaceholderText("保存在系统凭据库，不写入 QSettings")
        save_button = QPushButton("保存并刷新")
        save_button.clicked.connect(self.save_settings)
        manage_skills = QPushButton("管理 Skills")
        manage_skills.setObjectName("manage_skills")
        manage_skills.clicked.connect(self.open_skill_manager)
        manage_accounts = QPushButton("管理账号")
        manage_accounts.setObjectName("manage_accounts")
        manage_accounts.clicked.connect(self.open_account_manager)
        manage_knowledge = QPushButton("知识库")
        manage_knowledge.setObjectName("manage_knowledge")
        manage_knowledge.clicked.connect(self.open_knowledge_manager)
        manage_reminders = QPushButton("提醒")
        manage_reminders.setObjectName("manage_reminders")
        manage_reminders.clicked.connect(self.open_reminder_manager)
        connection_actions = QHBoxLayout()
        connection_actions.addWidget(save_button)
        connection_actions.addWidget(manage_skills)
        connection_actions.addWidget(manage_accounts)
        connection_actions.addWidget(manage_knowledge)
        connection_actions.addWidget(manage_reminders)
        settings_form.addRow("API", self.api_url)
        settings_form.addRow("用户", self.user_id)
        settings_form.addRow("Token", self.api_token)
        settings_form.addRow("", connection_actions)
        layout.addWidget(settings_group)

        task_group = QGroupBox("新任务")
        task_layout = QVBoxLayout(task_group)
        self.task_mode = QComboBox()
        self.task_mode.setObjectName("task_mode")
        for label, value in (
            ("智能路由", "agent"),
            ("计划", "plan"),
            ("学习", "learn"),
            ("日报", "daily"),
            ("Office", "office"),
            ("记忆", "memory"),
            ("状态", "status"),
        ):
            self.task_mode.addItem(label, value)
        self.task_input = QPlainTextEdit()
        self.task_input.setObjectName("task_input")
        self.task_input.setPlaceholderText("输入要交给 Agent 的任务…")
        self.task_input.setMaximumHeight(76)
        submit_button = QPushButton("提交任务")
        submit_button.clicked.connect(self.submit_task)
        task_layout.addWidget(self.task_mode)
        task_layout.addWidget(self.task_input)
        task_layout.addWidget(submit_button)
        layout.addWidget(task_group)

        self.status_label = QLabel("请配置已有用户 ID。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        tasks_group = QGroupBox("最近任务")
        tasks_layout = QVBoxLayout(tasks_group)
        self.recent_tasks = QListWidget()
        self.recent_tasks.setObjectName("recent_tasks")
        self.recent_tasks.setMaximumHeight(115)
        self.recent_tasks.currentItemChanged.connect(self._task_selected)
        tasks_layout.addWidget(self.recent_tasks)
        self.task_result = QLabel("尚未选择任务")
        self.task_result.setObjectName("task_result")
        self.task_result.setWordWrap(True)
        self.task_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tasks_layout.addWidget(self.task_result)
        layout.addWidget(tasks_group)

        approvals_group = QGroupBox("待审批")
        approvals_layout = QVBoxLayout(approvals_group)
        self.approval_list = QListWidget()
        self.approval_list.setObjectName("approval_list")
        self.approval_list.setMaximumHeight(72)
        approvals_layout.addWidget(self.approval_list)
        decision_layout = QHBoxLayout()
        approve_button = QPushButton("批准")
        approve_button.clicked.connect(
            lambda: self.decide_selected_approval("approved")
        )
        reject_button = QPushButton("拒绝")
        reject_button.clicked.connect(
            lambda: self.decide_selected_approval("rejected")
        )
        decision_layout.addWidget(approve_button)
        decision_layout.addWidget(reject_button)
        approvals_layout.addLayout(decision_layout)
        layout.addWidget(approvals_group)

        self.setCentralWidget(root)

    def _build_tray(self) -> None:
        style = QApplication.style()
        icon = style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("个人 Agent 助手")
        tray_menu = QMenu(self)
        show_action = QAction("显示窗口", self)
        show_action.triggered.connect(self.show_and_raise)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.shutdown)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def save_settings(self) -> None:
        try:
            base_url, user_id = normalize_connection_settings(
                self.api_url.text(),
                self.user_id.text(),
            )
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        self.api_url.setText(base_url)
        self.user_id.setText(user_id)
        token = self.api_token.text().strip()
        if token:
            try:
                self.token_store.set(base_url=base_url, user_id=user_id, token=token)
            except Exception as exc:
                self.status_label.setText(str(exc))
                return
            self.api_token.clear()
        self.settings.setValue("api_base_url", base_url)
        self.settings.setValue("user_id", user_id)
        self.settings.sync()
        self.status_label.setText("连接设置已保存。")
        self.refresh_tasks()

    def submit_task(self) -> None:
        input_text = self.task_input.toPlainText().strip()
        if not input_text:
            self.status_label.setText("请输入任务内容。")
            return
        connection = self._connection()
        if connection is None:
            return
        task_type = str(self.task_mode.currentData())
        self.status_label.setText("正在提交任务…")
        self._start_request(
            "submit",
            lambda: self._with_client(
                connection,
                lambda client: client.submit_task(
                    task_type=task_type,
                    input_text=input_text,
                ),
            ),
            self._submitted,
        )

    def open_skill_manager(self) -> None:
        connection = self._connection()
        if connection is None:
            return
        if self._skill_dialog is not None:
            self._skill_dialog.close()
            self._skill_dialog.deleteLater()
        self._skill_dialog = SkillManagerDialog(
            base_url=connection[0],
            user_id=connection[1],
            parent=self,
            thread_pool=self.thread_pool,
            client_factory=self.client_factory,
            api_token=self._load_token(connection),
        )
        self._skill_dialog.show()
        self._skill_dialog.raise_()
        self._skill_dialog.activateWindow()
        self._skill_dialog.refresh_skills()

    def open_account_manager(self) -> None:
        connection = self._connection()
        if connection is None:
            return
        if self._account_dialog is not None:
            self._account_dialog.close()
            self._account_dialog.deleteLater()
        self._account_dialog = AccountManagerDialog(
            base_url=connection[0],
            user_id=connection[1],
            parent=self,
            thread_pool=self.thread_pool,
            client_factory=self.client_factory,
            api_token=self._load_token(connection),
        )
        self._account_dialog.show()
        self._account_dialog.raise_()
        self._account_dialog.activateWindow()
        self._account_dialog.refresh_connections()

    def open_knowledge_manager(self) -> None:
        connection = self._connection()
        if connection is None:
            return
        if self._knowledge_dialog is not None:
            self._knowledge_dialog.close()
            self._knowledge_dialog.deleteLater()
        self._knowledge_dialog = KnowledgeManagerDialog(
            base_url=connection[0],
            user_id=connection[1],
            parent=self,
            thread_pool=self.thread_pool,
            client_factory=self.client_factory,
            api_token=self._load_token(connection),
        )
        self._knowledge_dialog.show()
        self._knowledge_dialog.raise_()
        self._knowledge_dialog.activateWindow()
        self._knowledge_dialog.refresh_documents()

    def open_reminder_manager(self) -> None:
        connection = self._connection()
        if connection is None:
            return
        if self._reminder_dialog is not None:
            self._reminder_dialog.close()
            self._reminder_dialog.deleteLater()
        self._reminder_dialog = ReminderManagerDialog(
            base_url=connection[0],
            user_id=connection[1],
            parent=self,
            thread_pool=self.thread_pool,
            client_factory=self.client_factory,
            api_token=self._load_token(connection),
        )
        self._reminder_dialog.show()
        self._reminder_dialog.raise_()
        self._reminder_dialog.activateWindow()
        self._reminder_dialog.refresh_reminders()

    def refresh_tasks(self) -> None:
        connection = self._connection(show_error=False)
        if connection is None:
            return
        self._start_request(
            "tasks",
            lambda: self._with_client(connection, lambda client: client.list_tasks()),
            self._tasks_refreshed,
        )

    def poll_notifications(self) -> None:
        connection = self._connection(show_error=False)
        if connection is None:
            return
        self._start_request(
            "notifications",
            lambda: self._with_client(
                connection, lambda client: client.poll_notifications()
            ),
            self._notifications_polled,
        )

    def refresh_approvals(self) -> None:
        if self._current_task_id is None:
            self.approval_list.clear()
            return
        connection = self._connection(show_error=False)
        if connection is None:
            return
        task_id = self._current_task_id
        self._start_request(
            "approvals",
            lambda: self._with_client(
                connection,
                lambda client: client.list_approvals(task_id),
            ),
            self._approvals_refreshed,
        )

    def decide_selected_approval(self, decision: ApprovalDecision) -> None:
        item = self.approval_list.currentItem()
        if item is None or self._current_task_id is None:
            self.status_label.setText("请先选择一条待审批记录。")
            return
        approval = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(approval, dict):
            self.status_label.setText("审批记录格式无效。")
            return
        approval_id = str(approval.get("approval_id") or "")
        if not approval_id:
            self.status_label.setText("审批记录缺少 ID。")
            return
        connection = self._connection()
        if connection is None:
            return
        task_id = self._current_task_id
        self.status_label.setText("正在提交审批决定…")
        self._start_request(
            "decision",
            lambda: self._with_client(
                connection,
                lambda client: client.decide_approval(
                    task_id,
                    approval_id,
                    decision,
                ),
            ),
            self._approval_decided,
        )

    def _connection(self, *, show_error: bool = True) -> tuple[str, str] | None:
        try:
            return normalize_connection_settings(
                self.api_url.text(),
                self.user_id.text(),
            )
        except Exception as exc:
            if show_error:
                self.status_label.setText(str(exc))
            return None

    def _with_client(
        self,
        connection: tuple[str, str],
        operation: Callable[[DesktopApiClient], Any],
    ) -> Any:
        token = self._load_token(connection)
        kwargs: dict[str, str] = {"base_url": connection[0], "user_id": connection[1]}
        if token:
            kwargs["api_token"] = token
        client = self.client_factory(**kwargs)
        try:
            return operation(client)
        finally:
            client.close()

    def _load_token(self, connection: tuple[str, str]) -> str:
        try:
            return self.token_store.get(base_url=connection[0], user_id=connection[1])
        except Exception as exc:
            self.status_label.setText(str(exc))
            return ""

    def _start_request(
        self,
        key: str,
        operation: Callable[[], Any],
        on_success: SuccessHandler,
    ) -> None:
        if key in self._busy_operations:
            return
        self._busy_operations.add(key)
        worker = ApiWorker(operation)
        self._workers.add(worker)
        worker.signals.succeeded.connect(on_success)
        worker.signals.failed.connect(self.status_label.setText)
        worker.signals.finished.connect(
            lambda: self._request_finished(key, worker)
        )
        self.thread_pool.start(worker)

    def _request_finished(self, key: str, worker: ApiWorker) -> None:
        self._busy_operations.discard(key)
        self._workers.discard(worker)

    def _submitted(self, value: object) -> None:
        if not isinstance(value, SubmissionResult):
            self.status_label.setText("任务提交响应无效。")
            return
        self.task_input.clear()
        self._current_task_id = str(value.task.get("task_id") or "") or None
        self.status_label.setText(
            "任务已创建并入队。" if value.queued else "任务已创建，但当前未入队。"
        )
        self.refresh_tasks()
        self.refresh_approvals()

    def _tasks_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("任务列表响应无效。")
            return
        selected_task_id = self._current_task_id
        self.recent_tasks.clear()
        selected_item: QListWidgetItem | None = None
        for task in value[:20]:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("task_id") or "")
            status = str(task.get("status") or "unknown")
            task_type = str(task.get("task_type") or "task")
            item = QListWidgetItem(f"[{status}] {task_type} · {task_id[:8]}")
            item.setData(Qt.ItemDataRole.UserRole, task)
            self.recent_tasks.addItem(item)
            if task_id == selected_task_id:
                selected_item = item
        if selected_item is not None:
            self.recent_tasks.setCurrentItem(selected_item)
        elif self.recent_tasks.count() and self.recent_tasks.currentItem() is None:
            self.recent_tasks.setCurrentRow(0)

    def _task_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        if current is None:
            self._current_task_id = None
            self.task_result.setText("尚未选择任务")
            self.approval_list.clear()
            return
        task = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(task, dict):
            return
        self._current_task_id = str(task.get("task_id") or "") or None
        status = str(task.get("status") or "unknown")
        result = task.get("result_text") or task.get("error_message") or "暂无结果"
        self.task_result.setText(f"状态：{status}\n{result}")
        self.refresh_approvals()

    def _approvals_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("审批列表响应无效。")
            return
        self.approval_list.clear()
        for approval in value:
            if not isinstance(approval, dict) or approval.get("status") != "pending":
                continue
            approval_type = str(approval.get("approval_type") or "tool")
            type_label = {
                "tool": "工具",
                "plan": "计划",
                "review": "复核",
            }.get(approval_type, "审批")
            subject = str(
                approval.get("subject")
                or approval.get("tool_name")
                or "未知对象"
            )
            summary = str(approval.get("request_summary") or "").strip()
            label = f"[{type_label}] {subject}"
            if summary:
                label = f"{label} — {summary}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, approval)
            self.approval_list.addItem(item)
        if self.approval_list.count():
            self.approval_list.setCurrentRow(0)

    def _approval_decided(self, value: object) -> None:
        if not isinstance(value, ApprovalDecisionResult):
            self.status_label.setText("审批响应无效。")
            return
        status = str(value.approval.get("status") or "unknown")
        if status == "approved":
            message = "审批已通过并重新入队。" if value.queued else "审批已通过，任务待入队。"
        else:
            message = "审批已拒绝，任务已取消。"
        self.status_label.setText(message)
        self.refresh_tasks()
        self.refresh_approvals()

    def _notifications_polled(self, value: object) -> None:
        if not isinstance(value, list):
            return
        connection = self._connection(show_error=False)
        if connection is None:
            return
        for notification in value:
            if not isinstance(notification, dict):
                continue
            outbox_id = str(notification.get("outbox_id") or "")
            if not outbox_id:
                continue
            self.tray_icon.showMessage(
                str(notification.get("title") or "个人 Agent 提醒"),
                str(notification.get("message") or ""),
                QSystemTrayIcon.MessageIcon.Information,
                10_000,
            )
            self._start_request(
                f"notification-ack:{outbox_id}",
                partial(self._ack_notification, connection, outbox_id),
                lambda value: None,
            )

    def _ack_notification(
        self, connection: tuple[str, str], outbox_id: str
    ) -> None:
        self._with_client(
            connection,
            lambda client: client.acknowledge_notification(outbox_id),
        )

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_and_raise()

    def show_and_raise(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quitting:
            event.accept()
            return
        self.hide()
        event.ignore()

    def shutdown(self) -> None:
        self._quitting = True
        self.refresh_timer.stop()
        if self._skill_dialog is not None:
            self._skill_dialog.close()
        if self._account_dialog is not None:
            self._account_dialog.close()
        if self._knowledge_dialog is not None:
            self._knowledge_dialog.close()
        if self._reminder_dialog is not None:
            self._reminder_dialog.close()
        self.tray_icon.hide()
        self.close()
        application = QApplication.instance()
        if application is not None:
            application.quit()
