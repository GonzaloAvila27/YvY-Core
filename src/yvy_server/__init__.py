"""YVY Server — package root.

Version constants are populated at build time by ``scripts/embed_version.py``
which writes ``_version.py``. In dev (no build step), fallback values are used.
"""

try:
    from yvy_server._version import (  # type: ignore  # generado en build; ausente en source/CI
        __build_date__,
        __git_commit_short__,
        __version__,
    )
except ImportError:
    __version__ = "0.9.1-dev+unknown"
    __git_commit_short__ = "unknown"
    __build_date__ = "unknown"

__all__ = ["__build_date__", "__git_commit_short__", "__version__"]
