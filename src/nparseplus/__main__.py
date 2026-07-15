"""nParse+ entry point: ``python -m nparseplus`` or the ``nparseplus`` script."""

import sys


def main() -> int:
    try:  # PyInstaller splash screen, if present
        import pyi_splash

        pyi_splash.update_text("Done!")
        pyi_splash.close()
    except Exception:
        pass

    # Deferred: nparseplus.app fixes the CWD (data/ + nparse.config.json are
    # CWD-relative) before the legacy modules import.
    from nparseplus.app import run_app

    return run_app(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
