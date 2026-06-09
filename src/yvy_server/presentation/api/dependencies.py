"""Presentation · API — re-export of FastAPI dependencies.

The canonical definitions live in ``application.auth.dependencies`` (close to
the application logic). This shim lets routers import from a consistent
``presentation.api.dependencies`` path without coupling them to the application
layer's internal structure.
"""

from yvy_server.application.auth.dependencies import (
    get_credential_repo,
    get_edge_release_repo,
    get_event_use_case,
    get_observation_use_case,
    get_sensor_use_case,
    get_session_dep,
    get_session_factory_dep,
    get_settings_dep,
    get_station_repo,
    verify_admin_token,
)

__all__ = [
    "get_credential_repo",
    "get_edge_release_repo",
    "get_event_use_case",
    "get_observation_use_case",
    "get_sensor_use_case",
    "get_session_dep",
    "get_session_factory_dep",
    "get_settings_dep",
    "get_station_repo",
    "verify_admin_token",
]
