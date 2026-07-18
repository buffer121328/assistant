from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from .client import DesktopApiError


class WorkerSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class ApiWorker(QRunnable):
    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except DesktopApiError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:
            self.signals.failed.emit("操作失败，请检查 API 服务状态。")
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


class StreamWorkerSignals(QObject):
    event_received = Signal(object)
    failed = Signal(str)
    finished = Signal()


class TaskStreamWorker(QRunnable):
    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = StreamWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            for event in self.operation():
                self.signals.event_received.emit(event)
        except DesktopApiError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:
            self.signals.failed.emit("任务事件流已断开。")
        finally:
            self.signals.finished.emit()
