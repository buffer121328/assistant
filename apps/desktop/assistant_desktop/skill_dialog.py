from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeAlias

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .client import DesktopApiClient
from .worker import ApiWorker


ClientFactory: TypeAlias = Callable[..., DesktopApiClient]
SuccessHandler: TypeAlias = Callable[[Any], None]


class SkillManagerDialog(QDialog):
    def __init__(
        self,
        *,
        base_url: str,
        user_id: str,
        parent: object | None = None,
        thread_pool: QThreadPool | None = None,
        client_factory: ClientFactory = DesktopApiClient,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.base_url = base_url
        self.user_id = user_id
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.client_factory = client_factory
        self._workers: set[ApiWorker] = set()
        self._busy_operations: set[str] = set()

        self.setWindowTitle("Skill 管理")
        self.resize(560, 640)
        self._build_ui()
        self._update_controls()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(QLabel("已发现的 Skills"))
        header.addStretch()
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_skills)
        header.addWidget(refresh_button)
        layout.addLayout(header)

        self.skill_list = QListWidget()
        self.skill_list.setObjectName("skill_list")
        self.skill_list.currentItemChanged.connect(self._skill_selected)
        layout.addWidget(self.skill_list)

        form = QFormLayout()
        self.skill_name = QLineEdit()
        self.skill_name.setObjectName("skill_name")
        self.skill_name.setPlaceholderText("例如 meeting-notes")
        self.skill_display_name = QLineEdit()
        self.skill_display_name.setObjectName("skill_display_name")
        self.skill_summary = QLineEdit()
        self.skill_summary.setObjectName("skill_summary")
        self.skill_instructions = QPlainTextEdit()
        self.skill_instructions.setObjectName("skill_instructions")
        self.skill_instructions.setPlaceholderText("描述这个 Skill 应如何工作")
        self.skill_instructions.setMaximumHeight(100)
        form.addRow("名称", self.skill_name)
        form.addRow("显示名", self.skill_display_name)
        form.addRow("摘要", self.skill_summary)
        form.addRow("说明", self.skill_instructions)
        layout.addLayout(form)

        create_row = QHBoxLayout()
        self.create_button = QPushButton("创建")
        self.create_button.setObjectName("create_skill")
        self.create_button.clicked.connect(self.create_skill)
        self.install_button = QPushButton("安装 ZIP")
        self.install_button.setObjectName("install_skill")
        self.install_button.clicked.connect(self.choose_and_install_skill)
        create_row.addWidget(self.create_button)
        create_row.addWidget(self.install_button)
        layout.addLayout(create_row)

        lifecycle_row = QHBoxLayout()
        self.enable_button = QPushButton("启用")
        self.enable_button.setObjectName("enable_skill")
        self.enable_button.clicked.connect(lambda: self.set_selected_enabled(True))
        self.disable_button = QPushButton("停用")
        self.disable_button.setObjectName("disable_skill")
        self.disable_button.clicked.connect(
            lambda: self.set_selected_enabled(False)
        )
        self.uninstall_button = QPushButton("卸载")
        self.uninstall_button.setObjectName("uninstall_skill")
        self.uninstall_button.clicked.connect(self.uninstall_selected)
        lifecycle_row.addWidget(self.enable_button)
        lifecycle_row.addWidget(self.disable_button)
        lifecycle_row.addWidget(self.uninstall_button)
        layout.addLayout(lifecycle_row)

        self.status_label = QLabel("点击刷新加载 Skills。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def refresh_skills(self) -> None:
        self.status_label.setText("正在刷新 Skills…")
        self._start_request(
            "list",
            lambda: self._with_client(lambda client: client.list_skills()),
            self._skills_refreshed,
        )

    def create_skill(self) -> None:
        name = self.skill_name.text().strip()
        display_name = self.skill_display_name.text().strip()
        summary = self.skill_summary.text().strip()
        instructions = self.skill_instructions.toPlainText().strip()
        if not all((name, display_name, summary, instructions)):
            self.status_label.setText("请完整填写名称、显示名、摘要和说明。")
            return
        self.status_label.setText("正在创建 Skill…")
        self._start_request(
            "mutate",
            lambda: self._with_client(
                lambda client: client.create_skill(
                    name=name,
                    display_name=display_name,
                    summary=summary,
                    instructions=instructions,
                )
            ),
            lambda value: self._mutation_succeeded(value, "Skill 已创建，默认停用。"),
        )

    def choose_and_install_skill(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Skill 安装包",
            "",
            "Skill ZIP (*.zip)",
        )
        if not filename:
            return
        self.status_label.setText("正在安装 Skill…")
        self._start_request(
            "mutate",
            lambda: self._with_client(
                lambda client: client.install_skill(Path(filename))
            ),
            lambda value: self._mutation_succeeded(value, "Skill 已安装，默认停用。"),
        )

    def set_selected_enabled(self, enabled: bool) -> None:
        skill = self._selected_skill()
        if skill is None:
            self.status_label.setText("请先选择可管理的 Skill。")
            return
        name = str(skill["name"])
        action = "启用" if enabled else "停用"
        self.status_label.setText(f"正在{action} {name}…")
        self._start_request(
            "mutate",
            lambda: self._with_client(
                lambda client: client.set_skill_enabled(name, enabled=enabled)
            ),
            lambda value: self._mutation_succeeded(value, f"Skill 已{action}。"),
        )

    def uninstall_selected(self) -> None:
        skill = self._selected_skill()
        if skill is None:
            self.status_label.setText("请先选择可管理的 Skill。")
            return
        name = str(skill["name"])
        decision = QMessageBox.question(
            self,
            "确认卸载",
            f"确定卸载 {name}？此操作不会影响内置 Skills。",
        )
        if decision != QMessageBox.StandardButton.Yes:
            return
        self.status_label.setText(f"正在卸载 {name}…")
        self._start_request(
            "mutate",
            lambda: self._with_client(lambda client: client.uninstall_skill(name)),
            lambda value: self._mutation_succeeded(value, "Skill 已卸载。"),
        )

    def _with_client(self, operation: Callable[[DesktopApiClient], Any]) -> Any:
        client = self.client_factory(base_url=self.base_url, user_id=self.user_id)
        try:
            return operation(client)
        finally:
            client.close()

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

    def _skills_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("Skill 列表响应无效。")
            return
        selected_name = ""
        selected = self._selected_skill(require_manageable=False)
        if selected is not None:
            selected_name = str(selected.get("name") or "")
        self.skill_list.clear()
        selected_item: QListWidgetItem | None = None
        for skill in value:
            if not isinstance(skill, dict):
                continue
            name = str(skill.get("name") or "")
            display_name = str(skill.get("display_name") or name)
            source = str(skill.get("source") or "unknown")
            state = "启用" if skill.get("enabled") else "停用"
            item = QListWidgetItem(f"{display_name} · {source} · {state}")
            item.setData(Qt.ItemDataRole.UserRole, skill)
            self.skill_list.addItem(item)
            if name == selected_name:
                selected_item = item
        if selected_item is not None:
            self.skill_list.setCurrentItem(selected_item)
        elif self.skill_list.count():
            self.skill_list.setCurrentRow(0)
        self.status_label.setText(f"已加载 {self.skill_list.count()} 个 Skills。")
        self._update_controls()

    def _mutation_succeeded(self, value: object, message: str) -> None:
        del value
        self.status_label.setText(message)
        self.refresh_skills()

    def _skill_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del current, previous
        self._update_controls()

    def _selected_skill(
        self,
        *,
        require_manageable: bool = True,
    ) -> dict[str, Any] | None:
        item = self.skill_list.currentItem()
        if item is None:
            return None
        skill = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(skill, dict):
            return None
        if require_manageable and not skill.get("manageable"):
            return None
        return skill

    def _update_controls(self) -> None:
        skill = self._selected_skill()
        enabled = bool(skill and skill.get("enabled"))
        self.enable_button.setEnabled(skill is not None and not enabled)
        self.disable_button.setEnabled(skill is not None and enabled)
        self.uninstall_button.setEnabled(skill is not None)
