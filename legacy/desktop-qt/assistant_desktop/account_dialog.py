from __future__ import annotations

from collections.abc import Callable
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


class AccountManagerDialog(QDialog):
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

        self.setWindowTitle("账号连接管理")
        self.resize(540, 560)
        self._build_ui()
        self._provider_changed()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("已保存连接（凭据不会回显）"))
        header.addStretch()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_connections)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.connection_list = QListWidget()
        self.connection_list.setObjectName("connection_list")
        layout.addWidget(self.connection_list)

        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.setObjectName("connection_provider")
        self.provider.addItem("SMTP 邮件", "smtp")
        self.provider.addItem("CalDAV 日历", "caldav")
        self.provider.addItem("隔离浏览器", "browser")
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.display_name = QLineEdit()
        self.display_name.setObjectName("connection_display_name")
        self.endpoint = QLineEdit()
        self.endpoint.setObjectName("connection_endpoint")
        self.username = QLineEdit()
        self.username.setObjectName("connection_username")
        self.password = QLineEdit()
        self.password.setObjectName("connection_password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.port = QLineEdit("587")
        self.port.setObjectName("connection_port")
        self.security = QComboBox()
        self.security.setObjectName("connection_security")
        self.security.addItem("STARTTLS", "starttls")
        self.security.addItem("TLS/SSL", "ssl")
        form.addRow("类型", self.provider)
        form.addRow("名称", self.display_name)
        form.addRow("服务器", self.endpoint)
        form.addRow("用户名", self.username)
        form.addRow("密码/应用密码", self.password)
        form.addRow("SMTP 端口", self.port)
        form.addRow("SMTP 安全", self.security)
        layout.addLayout(form)

        actions = QHBoxLayout()
        for name, object_name, callback in (
            ("保存连接", "create_connection", self.create_connection),
            ("测试", "test_connection", self.test_selected),
            ("停用", "disable_connection", self.disable_selected),
            ("撤销", "revoke_connection", self.revoke_selected),
        ):
            button = QPushButton(name)
            button.setObjectName(object_name)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)

        self.status_label = QLabel("点击刷新加载账号连接。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def refresh_connections(self) -> None:
        self._start_request(
            "list",
            lambda: self._with_client(lambda client: client.list_connections()),
            self._connections_refreshed,
        )

    def create_connection(self) -> None:
        provider = str(self.provider.currentData())
        display_name = self.display_name.text().strip()
        endpoint = self.endpoint.text().strip()
        username = self.username.text().strip()
        password = self.password.text()
        if not display_name or not endpoint:
            self.status_label.setText("请完整填写名称和服务器/允许域名。")
            return
        if provider != "browser" and not all((username, password)):
            self.status_label.setText("请完整填写名称、服务器、用户名和密码。")
            return
        if provider == "smtp":
            credentials = {
                "host": endpoint,
                "port": self.port.text().strip(),
                "username": username,
                "password": password,
                "security": str(self.security.currentData()),
            }
        elif provider == "caldav":
            credentials = {"url": endpoint, "username": username, "password": password}
        else:
            credentials = {"allowed_domains": endpoint, "storage_state": "{}"}
        self._start_request(
            "mutate",
            lambda: self._with_client(
                lambda client: client.create_connection(
                    provider=provider,
                    display_name=display_name,
                    credentials=credentials,
                )
            ),
            lambda value: self._mutation_succeeded(value, "账号连接已保存。"),
        )

    def test_selected(self) -> None:
        self._mutate_selected("test", "账号连接测试完成。")

    def disable_selected(self) -> None:
        self._mutate_selected("disable", "账号连接已停用。")

    def revoke_selected(self) -> None:
        self._mutate_selected("revoke", "账号连接已撤销，凭据不可恢复。")

    def _mutate_selected(self, action: str, message: str) -> None:
        connection = self._selected_connection()
        if connection is None:
            self.status_label.setText("请先选择一个账号连接。")
            return
        connection_id = str(connection.get("connection_id") or "")
        operations = {
            "test": lambda client: client.test_connection(connection_id),
            "disable": lambda client: client.disable_connection(connection_id),
            "revoke": lambda client: client.revoke_connection(connection_id),
        }
        self._start_request(
            "mutate",
            lambda: self._with_client(operations[action]),
            lambda value: self._mutation_succeeded(value, message),
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

    def _connections_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("账号连接列表响应无效。")
            return
        self.connection_list.clear()
        for connection in value:
            if not isinstance(connection, dict):
                continue
            label = (
                f"{connection.get('display_name', '')} · "
                f"{connection.get('provider', '')} · {connection.get('status', '')}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, connection)
            self.connection_list.addItem(item)
        if self.connection_list.count():
            self.connection_list.setCurrentRow(0)
        self.status_label.setText(f"已加载 {self.connection_list.count()} 个账号连接。")

    def _mutation_succeeded(self, value: object, message: str) -> None:
        del value
        self.password.clear()
        self.username.clear()
        self.endpoint.clear()
        self.status_label.setText(message)
        self.refresh_connections()

    def _selected_connection(self) -> dict[str, Any] | None:
        item = self.connection_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return value if isinstance(value, dict) else None

    def _provider_changed(self) -> None:
        is_smtp = self.provider.currentData() == "smtp"
        is_browser = self.provider.currentData() == "browser"
        self.endpoint.setPlaceholderText(
            "smtp.example.com"
            if is_smtp
            else (
                "example.com,accounts.example.com"
                if is_browser
                else "https://calendar.example.com/dav"
            )
        )
        self.port.setVisible(is_smtp)
        self.security.setVisible(is_smtp)
        self.username.setVisible(not is_browser)
        self.password.setVisible(not is_browser)
