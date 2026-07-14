"""nParse+ entry point: ``python -m nparseplus`` or the ``nparseplus`` script."""

import os
import sys
from pathlib import Path

from PySide6.QtGui import QFontDatabase, QIcon

from nparseplus.helpers import resource_path
from nparseplus.helpers.application import NomnsParse


def _ensure_data_cwd() -> None:
    """Legacy modules open ``data/...`` relative to CWD.

    Until data moves into the package (M1), locate the project root that
    holds ``data/`` and chdir there when running from a source checkout.
    """
    if Path("data").is_dir():
        return
    for parent in Path(__file__).resolve().parents:
        if (parent / "data").is_dir():
            os.chdir(parent)
            return


def main() -> int:
    _ensure_data_cwd()
    try:  # PyInstaller splash screen, if present
        import pyi_splash

        pyi_splash.update_text("Done!")
        pyi_splash.close()
    except Exception:
        pass

    app = NomnsParse(sys.argv)
    with open(resource_path(os.path.join("data", "ui", "_.css"))) as css:
        app.setStyleSheet(css.read())
    app.setWindowIcon(QIcon(resource_path(os.path.join("data", "ui", "icon.png"))))
    app.setQuitOnLastWindowClosed(False)
    QFontDatabase.addApplicationFont(
        resource_path(os.path.join("data", "fonts", "NotoSans-Regular.ttf"))
    )
    QFontDatabase.addApplicationFont(
        resource_path(os.path.join("data", "fonts", "NotoSans-Bold.ttf"))
    )
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
