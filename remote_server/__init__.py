"""Remote Server Gateway - Unified entry point for remote server management.

This module provides a unified gateway with automatic health checking and
self-healing capabilities for remote server operations.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any
import subprocess
import time

from remote_tmux.config import RemoteProfile, load_remote_profiles
from remote_tmux.manager import RemoteTmuxManager


class ServiceStatus(Enum):
    """Service status enumeration."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthReport:
    """Health check report."""
    ssh_status: ServiceStatus
    tmux_status: ServiceStatus
    bmc_status: ServiceStatus
    server_responsive: bool
    details: Dict[str, Any]
    timestamp: float


# Import heartbeat after defining ServiceStatus
from remote_server.heartbeat import (
    HeartbeatMonitor,
    HeartbeatConfig,
    HeartbeatState,
    monitor_server_with_heartbeat,
)

__all__ = [
    "RemoteGateway",
    "ServiceStatus",
    "HealthReport",
    "HeartbeatMonitor",
    "HeartbeatConfig",
    "HeartbeatState",
    "monitor_server_with_heartbeat",
]


class RemoteGateway:
    """Unified gateway for remote server management.

    Provides:
    - Automatic health checking
    - Self-healing capabilities
    - Unified interface for tmux and BMC operations
    - Status monitoring without AI intervention
    """

    def __init__(self, profile_name: str, config_root: Optional[str] = None):
        """Initialize gateway.

        Args:
            profile_name: Profile name to use
            config_root: Optional config root directory
        """
        from pathlib import Path

        if config_root is None:
            # Try user config first, then project config
            user_config = Path.home() / ".config"
            if (user_config / "remote_tmux").exists():
                config_root = user_config
            else:
                config_root = Path.cwd()
        else:
            config_root = Path(config_root)

        profiles = load_remote_profiles(config_root)
        if profile_name not in profiles:
            raise ValueError(f"Profile '{profile_name}' not found")

        self.profile = profiles[profile_name]
        self.tmux = RemoteTmuxManager()
        self._last_health_check: Optional[HealthReport] = None
        self._health_check_interval = 60  # seconds

    def check_health(self, force: bool = False) -> HealthReport:
        """Check health of all services.

        Args:
            force: Force check even if recently checked

        Returns:
            HealthReport with status of all services
        """
        now = time.time()

        # Return cached result if recent
        if not force and self._last_health_check:
            age = now - self._last_health_check.timestamp
            if age < self._health_check_interval:
                return self._last_health_check

        details = {}

        # Check SSH connectivity
        ssh_status = self._check_ssh(details)

        # Check tmux session
        tmux_status = self._check_tmux(details)

        # Check BMC (if configured)
        bmc_status = self._check_bmc(details)

        # Overall server responsiveness
        server_responsive = ssh_status == ServiceStatus.HEALTHY

        report = HealthReport(
            ssh_status=ssh_status,
            tmux_status=tmux_status,
            bmc_status=bmc_status,
            server_responsive=server_responsive,
            details=details,
            timestamp=now
        )

        self._last_health_check = report
        return report

    def _check_ssh(self, details: Dict[str, Any]) -> ServiceStatus:
        """Check SSH connectivity."""
        try:
            cmd = [
                "ssh",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                self.profile.ssh_target,
                "echo ok"
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                details["ssh"] = "Connected"
                return ServiceStatus.HEALTHY
            else:
                details["ssh"] = f"Failed: {result.stderr.strip()}"
                return ServiceStatus.UNHEALTHY

        except subprocess.TimeoutExpired:
            details["ssh"] = "Timeout"
            return ServiceStatus.UNHEALTHY
        except Exception as e:
            details["ssh"] = f"Error: {str(e)}"
            return ServiceStatus.UNKNOWN

    def _check_tmux(self, details: Dict[str, Any]) -> ServiceStatus:
        """Check tmux session status."""
        try:
            cmd = self.tmux.build_status_command(self.profile)
            result = self.tmux.execute(cmd, check=False)

            if result.returncode == 0:
                # Parse output to count windows
                output = result.stdout
                if "active" in output:
                    lines = output.strip().split("\n")
                    window_count = len([l for l in lines if l.strip().startswith("#")])
                    details["tmux"] = f"Session active ({window_count} windows)"
                    return ServiceStatus.HEALTHY
                else:
                    details["tmux"] = "Session not running"
                    return ServiceStatus.DEGRADED
            else:
                details["tmux"] = "Cannot check status"
                return ServiceStatus.UNKNOWN

        except Exception as e:
            details["tmux"] = f"Error: {str(e)}"
            return ServiceStatus.UNKNOWN

    def _check_bmc(self, details: Dict[str, Any]) -> ServiceStatus:
        """Check BMC availability."""
        # BMC check will be implemented when BMC module is added
        details["bmc"] = "Not configured"
        return ServiceStatus.UNKNOWN

    def ensure_healthy(self, auto_recover: bool = True) -> bool:
        """Ensure all services are healthy.

        Args:
            auto_recover: Attempt automatic recovery if unhealthy

        Returns:
            True if healthy or recovered, False otherwise
        """
        report = self.check_health(force=True)

        if report.ssh_status == ServiceStatus.HEALTHY:
            return True

        if not auto_recover:
            return False

        # Attempt recovery
        # TODO: Implement recovery strategies
        # - SSH unhealthy -> try BMC reset
        # - Tmux degraded -> create session

        return False

    def get_status_summary(self) -> str:
        """Get human-readable status summary."""
        report = self.check_health()

        lines = []
        lines.append(f"Profile: {self.profile.name}")
        lines.append(f"Target: {self.profile.ssh_target}")
        lines.append("")

        # SSH status
        icon = self._status_icon(report.ssh_status)
        lines.append(f"{icon} SSH: {report.details.get('ssh', 'Unknown')}")

        # Tmux status
        icon = self._status_icon(report.tmux_status)
        lines.append(f"{icon} Tmux: {report.details.get('tmux', 'Unknown')}")

        # BMC status
        icon = self._status_icon(report.bmc_status)
        lines.append(f"{icon} BMC: {report.details.get('bmc', 'Unknown')}")

        # Overall
        lines.append("")
        if report.server_responsive:
            lines.append("✓ Server: Responsive")
        else:
            lines.append("✗ Server: Not responsive")

        return "\n".join(lines)

    def _status_icon(self, status: ServiceStatus) -> str:
        """Get icon for status."""
        return {
            ServiceStatus.HEALTHY: "✓",
            ServiceStatus.DEGRADED: "⚠",
            ServiceStatus.UNHEALTHY: "✗",
            ServiceStatus.UNKNOWN: "?",
        }[status]
