from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeAlias

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
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


class KnowledgeManagerDialog(QDialog):
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
        self.setWindowTitle("个人知识库")
        self.resize(600, 600)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        document_header = QHBoxLayout()
        document_header.addWidget(QLabel("已索引文档"))
        document_header.addStretch()
        upload = QPushButton("导入文件")
        upload.setObjectName("import_knowledge")
        upload.clicked.connect(self.choose_and_import)
        refresh = QPushButton("刷新状态")
        refresh.clicked.connect(self.refresh_documents)
        document_header.addWidget(upload)
        document_header.addWidget(refresh)
        layout.addLayout(document_header)

        self.document_list = QListWidget()
        self.document_list.setObjectName("knowledge_documents")
        layout.addWidget(self.document_list)

        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setObjectName("knowledge_query")
        self.query.setPlaceholderText("搜索个人知识库")
        search = QPushButton("搜索")
        search.setObjectName("search_knowledge")
        search.clicked.connect(self.search)
        search_row.addWidget(self.query)
        search_row.addWidget(search)
        layout.addLayout(search_row)

        self.result_list = QListWidget()
        self.result_list.setObjectName("knowledge_results")
        layout.addWidget(self.result_list)
        self.status_label = QLabel("请选择导入文件或搜索。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def choose_and_import(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择知识库文件",
            "",
            "文档 (*.txt *.md *.pdf *.docx *.xlsx *.pptx)",
        )
        if not filename:
            return
        self._start_request(
            "import",
            lambda: self._with_client(
                lambda client: client.import_knowledge(Path(filename))
            ),
            self._imported,
        )

    def refresh_documents(self) -> None:
        self._start_request(
            "documents",
            lambda: self._with_client(
                lambda client: client.list_knowledge_documents()
            ),
            self._documents_refreshed,
        )

    def search(self) -> None:
        query = self.query.text().strip()
        if not query:
            self.status_label.setText("请输入检索词。")
            return
        self._start_request(
            "search",
            lambda: self._with_client(lambda client: client.search_knowledge(query)),
            self._search_refreshed,
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

    def _imported(self, value: object) -> None:
        if not isinstance(value, dict):
            self.status_label.setText("导入响应无效。")
            return
        state = "内容未变化" if value.get("unchanged") else "索引完成"
        self.status_label.setText(f"{value.get('source_label', '文档')}：{state}。")
        self.refresh_documents()

    def _documents_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("文档状态响应无效。")
            return
        self.document_list.clear()
        for document in value:
            if not isinstance(document, dict):
                continue
            item = QListWidgetItem(
                f"{document.get('source_label', '')} · {document.get('status', '')} · "
                f"{document.get('chunk_count', 0)} chunks"
            )
            item.setData(Qt.ItemDataRole.UserRole, document)
            self.document_list.addItem(item)
        self.status_label.setText(f"已加载 {self.document_list.count()} 个文档。")

    def _search_refreshed(self, value: object) -> None:
        if not isinstance(value, list):
            self.status_label.setText("检索响应无效。")
            return
        self.result_list.clear()
        for result in value:
            if not isinstance(result, dict):
                continue
            item = QListWidgetItem(
                f"{result.get('source_label', '')} · {result.get('content', '')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.result_list.addItem(item)
        self.status_label.setText(f"找到 {self.result_list.count()} 条结果。")
