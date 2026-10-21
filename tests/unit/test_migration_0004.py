"""La migración 0004 (uptime_30d) no altera nada más que el CTE ``uptime``.

- El downgrade restaura exactamente la definición de v_network_status de la
  0003 (copia literal).
- La vista nueva es idéntica a la de la 0003 salvo el CTE ``uptime``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load(filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        filename.removesuffix(".py"), _VERSIONS / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M3 = _load("003_m0_canonical_model.py")
_M4 = _load("004_fix_network_uptime.py")


def _without_uptime_cte(sql: str) -> str:
    return re.sub(r"  uptime AS \(.*?\n  \)\n", "  uptime AS (...)\n", sql, flags=re.DOTALL)


def test_downgrade_restaura_la_vista_de_la_0003() -> None:
    assert _M4._V_NETWORK_STATUS_0003 == _M3._V_NETWORK_STATUS


def test_vista_nueva_solo_cambia_el_cte_uptime() -> None:
    assert _without_uptime_cte(_M4._V_NETWORK_STATUS_0004) == _without_uptime_cte(
        _M3._V_NETWORK_STATUS
    )
    assert "yvy_network_uptime_30d(now())" in _M4._V_NETWORK_STATUS_0004


def test_revisiones_encadenadas() -> None:
    assert _M4.down_revision == _M3.revision
