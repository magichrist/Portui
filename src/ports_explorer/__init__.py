"""Ports Explorer — interactive macOS process & network explorer.

Inspection is strictly read-only. The one exception is the explicit two-key
terminate action (``k`` then ``k``), which sends SIGTERM and only escalates to
SIGKILL when the user asks for it a second time.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__", "main"]


def main() -> int:
    """Lazy entry point that avoids importing Textual for ``--version``/``--help``."""
    from .__main__ import main as _main

    return _main()
