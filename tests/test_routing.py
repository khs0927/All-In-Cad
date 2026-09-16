import pytest

from all_in_cad.defaults import default_desktop_adapters
from all_in_cad.models import AdapterChannel, HostKind
from all_in_cad.routing import CapabilityRouter, RoutingError


def test_analysis_prefers_autodesk_official_mcp() -> None:
    router = CapabilityRouter(default_desktop_adapters())
    assert router.route_analysis().channel == AdapterChannel.AUTODESK_OFFICIAL_MCP


def test_writes_prefer_zwcad_native() -> None:
    router = CapabilityRouter(default_desktop_adapters())
    assert router.route_write().adapter_id == "zwcad-2026-native"


def test_host_specific_write_can_route_to_autocad() -> None:
    router = CapabilityRouter(default_desktop_adapters())
    assert router.route_write(required_host=HostKind.AUTOCAD).adapter_id == "autocad-2027-native"


def test_verification_must_be_independent() -> None:
    router = CapabilityRouter(default_desktop_adapters())
    writer = router.route_write()
    verifiers = router.route_verifiers(writer=writer, minimum=2)
    assert {item.adapter_id for item in verifiers} == {"autocad-official-mcp", "ezdxf-headless"}


def test_missing_capability_fails_closed() -> None:
    router = CapabilityRouter(default_desktop_adapters())
    with pytest.raises(RoutingError):
        router.route_write("civil3d.corridor.write")
