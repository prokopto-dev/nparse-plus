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
#
# Icons need nothing special here: the whole repo-root data/ is bundled, so
# data/ui/icon*.png (which ui/appicon.py reads by name, one file per size) and
# data/assets/icon.svg ride along, and the two this file names by path are
# regenerated in place by tools/gen_icons.py + packaging/make_icns.sh.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is a PyInstaller global

# Read the version straight out of the source of truth rather than letting CI
# patch Info.plist after the fact: every build (local or CI) then reports the
# same version Finder/Spotlight show as the app shows in its settings window.
# nparseplus isn't importable here (the spec runs under PyInstaller's own
# interpreter, before the Analysis), so parse the assignment.
# appversion.py is a sibling of this spec; the parsing and the Windows
# version-tuple arithmetic live there so they can be tested (see
# tests/test_release_workflow.py), which they cannot be from inside a spec.
sys.path.insert(0, str(ROOT / "packaging"))
from appversion import read_version, windows_version_tuple  # noqa: E402

VERSION = read_version((ROOT / "src" / "nparseplus" / "__init__.py").read_text(encoding="utf-8"))

datas = [
    (str(ROOT / "data"), "data"),
    (str(ROOT / "src" / "nparseplus" / "data"), "nparseplus/data"),
]

# Ship the dist-info of both distributions. A frozen app has no site-packages,
# so importlib.metadata finds nothing unless the metadata is bundled — and
# third-party plugin code legitimately calls
# importlib.metadata.version("nparseplus-sdk") (or "nparseplus") to branch on
# what it is running inside. Note the app's own SDK_VERSION/__version__ do NOT
# depend on this: both are plain literals read from their __init__.py, on
# purpose, because a metadata lookup silently fails exactly here.
datas += copy_metadata("nparseplus")
datas += copy_metadata("nparseplus-sdk")

a = Analysis(  # noqa: F821
    [str(ROOT / "src" / "nparseplus" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    # Plugins import these at runtime; app code paths PyInstaller traces
    # don't necessarily touch them, so declare them explicitly.
    hiddenimports=[
        "nparseplus_sdk",
        "nparseplus_sdk.compat",
        "nparseplus_sdk.events",
        "nparseplus_sdk.loading",
        "nparseplus_sdk.plugin",
        "nparseplus_sdk.testing",
        "nparseplus_sdk.timers",
        "nparseplus_sdk.ui",
        "nparseplus_sdk.validate",
        "nparseplus.ui.pluginwindow",
        # jeepney (the Flatpak portal client, #74) deliberately does NOT
        # appear here. Its every import — including the function-scoped
        # `from jeepney.io.blocking import open_dbus_connection` — is a plain
        # import statement, so modulegraph traces jeepney, .io.blocking,
        # .bus_messages, .auth, .bus, .fds, .low_level and .wrappers on its
        # own, and skips the asyncio/trio backends nothing imports. It is a
        # Linux-only dependency (see pyproject): on the macOS and Windows
        # builds it is simply absent, which nparseplus/flatpakportal.py's
        # guarded import already handles.
    ],
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
# Splash is Windows-only: unsupported on macOS, and it needs tkinter, which
# the uv-managed Python on the Linux CI runners doesn't ship. It also
# rasterizes splash.png via Pillow at build time, so Pillow lives in the
# `build` dependency group (it is NOT a runtime import — do not move it back
# to [project.dependencies]).
if sys.platform == "win32":
    splash = Splash(  # noqa: F821
        str(ROOT / "splash.png"),
        binaries=a.binaries,
        datas=a.datas,
        text_pos=None,
    )
    splash_args = [splash, splash.binaries]

# Windows-only VERSIONINFO resource, so Explorer's Details tab and
# `(Get-Item nparseplus.exe).VersionInfo` report the real version instead of
# nothing. Built as a structure rather than a .txt template so there is still
# exactly one source of truth (VERSION, above). The module imports pefile,
# which PyInstaller only pulls in on Windows — hence the guarded import.
version_args = {}
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    # A beta version ("2.30.0-beta.1") has no integer in its third
    # dot-separated field, so this is not the one-liner it looks like — see
    # appversion.windows_version_tuple.
    _parts = windows_version_tuple(VERSION)
    version_args["version"] = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_parts, prodvers=_parts),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",  # US English, Unicode
                        [
                            StringStruct("CompanyName", "prokopto-dev"),
                            StringStruct("FileDescription", "nParse+"),
                            StringStruct("FileVersion", VERSION),
                            StringStruct("InternalName", "nparseplus"),
                            StringStruct("OriginalFilename", "nparseplus.exe"),
                            StringStruct("ProductName", "nParse+"),
                            StringStruct("ProductVersion", VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

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
    **version_args,
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
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            # Tray app: no Dock icon would be LSUIElement, but the overlay
            # windows are easier to find with a Dock presence in 1.0.
        },
    )
