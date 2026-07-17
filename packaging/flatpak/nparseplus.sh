#!/bin/sh
# Flatpak launcher: the PyInstaller onedir bundle lives in /app/opt/nparseplus.

# Default to XWayland. The overlay recipe (ui/overlaybase.py) needs
# keep-above, self-positioning/drag, and setWindowOpacity — all silently
# unsupported on native Wayland, all honored for X11 clients. EQ under WINE
# is X11 too, so game and overlays share one window stack. Overridable so
# CI can smoke-test with offscreen (and users can experiment, at their own
# risk — see docs/install-flatpak.md).
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

# Chromium's sandbox can't nest inside the Flatpak sandbox (no unprivileged
# user namespaces under bwrap) — without this the Discord overlay's
# QtWebEngine render processes die and the view is blank.
export QTWEBENGINE_DISABLE_SANDBOX=1

exec /app/opt/nparseplus/nparseplus "$@"
