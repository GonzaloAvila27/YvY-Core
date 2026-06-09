"""Presentation · API · Routers — One module per endpoint family.

Each module exposes ``router`` (an ``APIRouter`` instance). The
composition root in ``main.py`` registers them on the FastAPI app.
"""
from yvy_server.presentation.api.routers import admin, edge, health, ingest

__all__ = ["admin", "edge", "health", "ingest"]
