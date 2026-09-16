from __future__ import annotations

from collections.abc import Iterable

from .models import AdapterChannel, AdapterDescriptor, HostKind


class RoutingError(RuntimeError):
    pass


class CapabilityRouter:
    """Deterministic host routing.

    Policy:
    - AutoCAD Official MCP is preferred for read/analysis, never as the default writer.
    - ZWCAD native is preferred for normal writes.
    - AutoCAD native is the second native writer and is required for AutoCAD-specific work.
    - ZWCAD LISP/File-IPC is a fallback, not the first writer.
    - Independent verification prefers a channel different from the writer.
    """

    def __init__(self, adapters: Iterable[AdapterDescriptor]):
        self._adapters = tuple(adapters)

    @property
    def adapters(self) -> tuple[AdapterDescriptor, ...]:
        return self._adapters

    def _candidates(self, capability: str) -> list[AdapterDescriptor]:
        return [a for a in self._adapters if a.supports(capability)]

    def route_analysis(self, capability: str = "drawing.analyze") -> AdapterDescriptor:
        candidates = self._candidates(capability)
        order = {
            AdapterChannel.AUTODESK_OFFICIAL_MCP: 0,
            AdapterChannel.AUTOCAD_NATIVE: 1,
            AdapterChannel.ZWCAD_NATIVE: 2,
            AdapterChannel.EZDXF: 3,
            AdapterChannel.ZWCAD_LISP_FALLBACK: 4,
        }
        if not candidates:
            raise RoutingError(f"no online adapter provides {capability}")
        return min(candidates, key=lambda a: (order[a.channel], a.adapter_id))

    def route_write(
        self,
        capability: str = "entity.write",
        *,
        required_host: HostKind | None = None,
    ) -> AdapterDescriptor:
        candidates = self._candidates(capability)
        if required_host is not None:
            candidates = [a for a in candidates if a.host == required_host]
        candidates = [
            a for a in candidates if a.channel != AdapterChannel.AUTODESK_OFFICIAL_MCP
        ]
        if not candidates:
            suffix = f" for {required_host}" if required_host else ""
            raise RoutingError(f"no approved write adapter provides {capability}{suffix}")

        def score(adapter: AdapterDescriptor) -> tuple[int, int, str]:
            channel_rank = {
                AdapterChannel.ZWCAD_NATIVE: 0,
                AdapterChannel.AUTOCAD_NATIVE: 1,
                AdapterChannel.ZWCAD_LISP_FALLBACK: 2,
                AdapterChannel.EZDXF: 3,
            }.get(adapter.channel, 9)
            preferred_rank = 0 if adapter.preferred_for_writes else 1
            return (preferred_rank, channel_rank, adapter.adapter_id)

        return min(candidates, key=score)

    def route_verifiers(
        self,
        *,
        writer: AdapterDescriptor,
        capability: str = "drawing.verify",
        minimum: int = 2,
    ) -> tuple[AdapterDescriptor, ...]:
        candidates = [a for a in self._candidates(capability) if a.adapter_id != writer.adapter_id]
        independent = [a for a in candidates if a.channel != writer.channel]
        ordered = sorted(
            independent,
            key=lambda a: (
                0 if a.channel == AdapterChannel.AUTODESK_OFFICIAL_MCP else 1,
                0 if a.channel == AdapterChannel.EZDXF else 1,
                0 if a.authoritative else 1,
                a.adapter_id,
            ),
        )
        if len(ordered) < minimum:
            raise RoutingError(
                f"need {minimum} independent verifiers for writer={writer.adapter_id}; "
                f"found {len(ordered)}"
            )
        return tuple(ordered[:minimum])
