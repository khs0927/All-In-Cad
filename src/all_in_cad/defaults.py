from __future__ import annotations

from .models import AdapterChannel, AdapterDescriptor, HostKind


def default_desktop_adapters() -> tuple[AdapterDescriptor, ...]:
    """Expected production topology for AutoCAD 2027 + ZWCAD 2026."""
    return (
        AdapterDescriptor(adapter_id="autocad-official-mcp", host=HostKind.AUTOCAD, host_version="2027", channel=AdapterChannel.AUTODESK_OFFICIAL_MCP, capabilities=frozenset({"drawing.analyze", "drawing.verify", "entity.read", "standards.check"}), authoritative=True),
        AdapterDescriptor(adapter_id="autocad-2027-native", host=HostKind.AUTOCAD, host_version="2027", channel=AdapterChannel.AUTOCAD_NATIVE, capabilities=frozenset({"entity.read", "entity.write", "drawing.verify", "transaction.native"})),
        AdapterDescriptor(adapter_id="zwcad-2026-native", host=HostKind.ZWCAD, host_version="2026", channel=AdapterChannel.ZWCAD_NATIVE, capabilities=frozenset({"entity.read", "entity.write", "drawing.verify", "transaction.native"}), preferred_for_writes=True),
        AdapterDescriptor(adapter_id="zwcad-2026-lisp-fallback", host=HostKind.ZWCAD, host_version="2026", channel=AdapterChannel.ZWCAD_LISP_FALLBACK, capabilities=frozenset({"entity.read", "entity.write", "visual.capture"})),
        AdapterDescriptor(adapter_id="ezdxf-headless", host=HostKind.HEADLESS, channel=AdapterChannel.EZDXF, capabilities=frozenset({"entity.read", "drawing.analyze", "drawing.verify"})),
    )
