"""App-side package: CDP client, identity, project guard, selectors, discovery.

Phase 1 is READ-ONLY: no clicks that create conversations, no sends.
"""

from .cdp_client import CdpClient, find_renderer
from .discovery import discover

__all__ = ["CdpClient", "find_renderer", "discover"]
