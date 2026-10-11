"""CLI entry point: argument parsing, logging setup, clean terminal exit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_THEME,
    build_settings,
    configure_logging,
    default_log_path,
    theme_names,
)

__all__ = ["build_parser", "main"]

DESCRIPTION = """\
Ports Explorer — an interactive terminal explorer for macOS processes,
network sockets and open file descriptors.

Read-only by design: it inspects, it never closes or deletes anything.

The one exception is an explicit two-key terminate action: press k twice to send
SIGTERM, and again to escalate to SIGKILL if the process ignores it. It never
escalates privileges on your behalf.
"""


def theme_choices() -> list[str]:
    return theme_names()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ports-explorer",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ports-explorer {__version__}",
        help="Show the version and exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=f"Write diagnostic logs to {default_log_path()} (never to the terminal).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write diagnostic logs to PATH instead of the default cache location.",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help=(
            "Seconds between automatic metric refreshes (default: 2). "
            "Press R in the app to pause or resume it. Use 0 to start paused."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        metavar="SECONDS",
        help="Timeout for a single lsof collection (default: 20).",
    )
    parser.add_argument(
        "--theme",
        choices=theme_choices(),
        default=DEFAULT_THEME,
        metavar="NAME",
        help=(
            "Colour theme to start with (default: "
            f"{DEFAULT_THEME}). One of: {', '.join(theme_choices())}. "
            "Press Ctrl+T in the app to switch themes live."
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable color output where the terminal supports it.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    log_file: Path | None = args.log_file
    if log_file is None and args.debug:
        log_file = default_log_path()
    configure_logging(debug=args.debug, log_file=log_file)
    build_settings(
        debug=args.debug,
        log_file=log_file,
        refresh_interval=args.refresh,
        lsof_timeout=args.timeout,
        theme=args.theme,
    )

    if args.no_color:
        import os

        os.environ["NO_COLOR"] = "1"
        os.environ["TEXTUAL_COLOR_SYSTEM"] = "none"

    from .app import PortsExplorerApp

    app = PortsExplorerApp(theme=args.theme)
    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
