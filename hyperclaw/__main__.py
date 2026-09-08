"""Compatibility entrypoint: ``python -m hyperclaw`` uses the installed CLI."""

from cli.hyperclaw import main


if __name__ == "__main__":
    main()
