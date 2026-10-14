import pytest
from fastapi.testclient import TestClient

from canaisdark.services import version


def test_current_reads_pyproject_and_channel() -> None:
    info = version.current()
    assert version.parse_semver(info.version)  # formato válido
    assert info.channel in ("stable", "dev")
    assert info.label.startswith(f"v{info.version}")


def test_parse_semver_orders_numerically_and_rejects_bad() -> None:
    assert version.parse_semver("v0.10.0") > version.parse_semver("0.9.3")
    for bad in ("1.0", "a.b.c", "1.0.0.1", ""):
        with pytest.raises(ValueError):
            version.parse_semver(bad)


def test_health_includes_version(client: TestClient) -> None:
    data = client.get("/health").json()
    assert data["status"] == "ok" and data["version"] == version.current().version
