from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .window import TaskWindow


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("AssistantDesktop")
    application.setOrganizationName("PersonalAgent")
    application.setQuitOnLastWindowClosed(False)
    window = TaskWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
