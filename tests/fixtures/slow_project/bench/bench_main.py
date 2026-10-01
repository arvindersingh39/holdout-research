"""Bench entrypoint: the harness imports this module and times main()."""

from slowpkg.core import compute


def main() -> None:
    compute(300_000)
