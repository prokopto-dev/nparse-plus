# PyInstaller onedir build for nParse+ (macOS .app / Windows dir / Linux dir).
#
#   uv run pyinstaller packaging/nparseplus.spec --noconfirm
#
# Two data roots must both land under sys._MEIPASS (app._ensure_data_cwd
# chdirs there when frozen):
#   repo-root data/       -> data/            (legacy CWD-relative loads:
#                                              maps, fonts, spells, ui css)
#   src/nparseplus/data/  -> nparseplus/data/ (importlib.resources package
#                                              data: zones, triggers,
#                                              visionfix.zip, ...)
# QtWebEngine stays bundled (Discord overlay works out of the box) — that is
# most of the bundle size; unused heavyweight Qt modules are excluded.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is a PyInstaller global

datas = [
    (str(ROOT / "data"), "data"),
    (str(ROOT / "src" / "nparseplus" / "data"), "nparseplus/data"),
]

a = Analysis(  # noqa: F821
    [str(ROOT / "src" / "nparseplus" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=[],
    excludes=[
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtGraphs",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtQuick3D",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSensors",
        "PySide6.QtSerialBus",
        "PySide6.QtSerialPort",
        "PySide6.QtSpatialAudio",
        "PySide6.QtTest",
        "PySide6.QtTextToSpeech",  # audio.tts shells out to `say` instead
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

splash_args = []
if sys.platform != "darwin":  # pyi_splash is unsupported on macOS
    splash = Splash(  # noqa: F821
        str(ROOT / "splash.png"),
        binaries=a.binaries,
        datas=a.datas,
        text_pos=None,
    )
    splash_args = [splash, splash.binaries]

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    *splash_args[:1],
    exclude_binaries=True,
    name="nparseplus",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "packaging" / "icon.icns")
    if sys.platform == "darwin"
    else str(ROOT / "data" / "ui" / "icon.ico"),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    *splash_args[1:],
    strip=False,
    upx=False,
    name="nparseplus",
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="nParse+.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="org.nparseplus.nparseplus",
        info_plist={
            "CFBundleName": "nParse+",
            "CFBundleDisplayName": "nParse+",
            "CFBundleShortVersionString": "0.0.0-dev",  # overwritten by CI
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            # Tray app: no Dock icon would be LSUIElement, but the overlay
            # windows are easier to find with a Dock presence in 1.0.
        },
    )
