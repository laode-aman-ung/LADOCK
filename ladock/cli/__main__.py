"""Allow ``python -m ladock.cli`` alongside the ``ladock-cli`` command."""

from ladock.cli.agent import main

if __name__ == "__main__":
    raise SystemExit(main())
