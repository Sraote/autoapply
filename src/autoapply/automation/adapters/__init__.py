from .base import ApplyContext, ApplyResult, ATSAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .manual import ManualAdapter

# Register new adapters here (order = precedence when matching by URL).
ADAPTERS: tuple[type[ATSAdapter], ...] = (GreenhouseAdapter, LeverAdapter)


def adapter_for(portal_type: str, url: str = "") -> type[ATSAdapter]:
    for adapter in ADAPTERS:
        if adapter.portal_type == portal_type or (url and adapter.handles(url)):
            return adapter
    return ManualAdapter


__all__ = [
    "ADAPTERS",
    "ATSAdapter",
    "ApplyContext",
    "ApplyResult",
    "GreenhouseAdapter",
    "LeverAdapter",
    "ManualAdapter",
    "adapter_for",
]
