"""Allow ``python -m ladock.desktop`` alongside the ``ladock-desktop`` command."""

from ladock.desktop.main import main

if __name__ == "__main__":
    raise SystemExit(main())
