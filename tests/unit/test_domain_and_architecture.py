"""F008 / INTEG: Onion boundaries, event envelope contract, Device identity rules."""
import ast
import json
from pathlib import Path

import pytest

from backend.domain.events import EnvelopeError, new_event, parse_event
from backend.domain.model import Capability, CapabilityStatus, Device, DeviceFamily, SourceType

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("fastapi", "sqlalchemy", "nats", "httpx", "paramiko", "starlette", "alembic", "psycopg", "backend.aruba",
             "backend.database", "backend.infrastructure", "backend.presentation")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if n.level:  # relative: resolve against backend.domain
                mod = "backend." + ("domain." if n.level == 1 else "") + mod
            out.add(mod)
    return out


@pytest.mark.parametrize("path", sorted((ROOT / "backend" / "domain").glob("*.py")), ids=lambda p: p.name)
def test_domain_imports_no_framework_orm_bus_or_vendor(path):
    bad = [m for m in _imports(path) if m.startswith(FORBIDDEN)]
    assert not bad, f"{path.name} imports {bad}"


def test_application_layer_does_not_import_frameworks():
    for path in (ROOT / "backend" / "application").glob("*.py"):
        bad = [m for m in _imports(path) if m.startswith(("fastapi", "sqlalchemy", "nats", "httpx", "starlette"))]
        assert not bad, f"{path.name} imports {bad}"


def test_envelope_round_trip_and_required_fields():
    e = new_event("device.telemetry.observed", source_service="network-worker", source_type="DEMO", actor_id="w",
                  legacy_site_id=2, payload={"points": [{"metric": "cpu_percent", "value": 12.5}]})
    raw = json.loads(e.to_json())
    for f in ("event_id", "schema_version", "occurred_at_utc", "emitted_at_utc", "correlation_id", "causation_id",
              "actor_type", "actor_id", "source_service", "site_id", "device_id", "event_type", "payload_ref",
              "integrity_hash", "data_quality", "source_type"):
        assert f in raw
    assert parse_event(e.to_json()) == e


def test_tampered_envelope_is_rejected():
    e = new_event("synthetic.probe", source_service="w", source_type="DEMO", actor_id="w")
    raw = json.loads(e.to_json())
    raw["source_type"] = "REAL"
    with pytest.raises(EnvelopeError, match="integrity"):
        parse_event(json.dumps(raw).encode())


def test_additive_optional_field_from_newer_producer_is_tolerated():
    from backend.domain.events import integrity_hash
    raw = json.loads(new_event("synthetic.probe", source_service="w", source_type="DEMO", actor_id="w").to_json())
    raw["future_optional_field"] = {"x": 1}
    raw["integrity_hash"] = integrity_hash(raw)
    assert parse_event(json.dumps(raw).encode()).event_type == "synthetic.probe"


def test_unknown_major_schema_version_rejected():
    raw = json.loads(new_event("synthetic.probe", source_service="w", source_type="DEMO", actor_id="w").to_json())
    raw["schema_version"] = "2.0"
    with pytest.raises(EnvelopeError, match="schema_version"):
        parse_event(json.dumps(raw).encode())


def test_unknown_event_type_and_source_type_refused():
    with pytest.raises(ValueError):
        new_event("device.config.write", source_service="w", source_type="DEMO", actor_id="w")
    with pytest.raises(ValueError):
        new_event("synthetic.probe", source_service="w", source_type="LAB", actor_id="w")


def test_demo_and_real_devices_never_share_identity():
    """SEC-02: same hostname and serial, different source type -> distinct identity keys."""
    a = Device(id="1", source_type=SourceType.DEMO, vendor="aruba", family=DeviceFamily.SWITCH, model="CX", serial_number="SN1", hostname="core-1")
    b = Device(id="2", source_type=SourceType.REAL, vendor="aruba", family=DeviceFamily.SWITCH, model="CX", serial_number="SN1", hostname="core-1")
    assert a.identity_key != b.identity_key


def test_write_capability_requires_certification():
    d = Device(id="1", source_type=SourceType.REAL, vendor="aruba", family=DeviceFamily.SWITCH, model="CX", serial_number="S",
               capabilities=[Capability("config.vlan", CapabilityStatus.UNVERIFIED), Capability("read.vlans", CapabilityStatus.READ_ONLY),
                             Capability("config.port", CapabilityStatus.READ_ONLY)])
    assert d.can("read.vlans")
    assert not d.can("config.vlan")
    assert not d.can("config.port")  # a write op can never be enabled by a READ_ONLY status
    assert not d.can("read.lldp")    # absent = unsupported
