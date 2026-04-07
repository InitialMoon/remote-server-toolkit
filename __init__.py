"""Remote Server Toolkit - Unified gateway for remote server management.

This package provides a comprehensive toolkit for managing remote servers:
- Tmux session management
- BMC hardware control
- Unified gateway with automatic health checking
- Self-healing capabilities
"""

from remote_server import RemoteGateway, HealthReport, ServiceStatus

__version__ = "0.2.0"
__all__ = ["RemoteGateway", "HealthReport", "ServiceStatus"]
