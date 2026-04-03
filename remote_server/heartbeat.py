"""Automatic heartbeat monitoring and reconnection for remote servers.

This module provides background heartbeat monitoring that:
- Continuously checks server health
- Automatically attempts recovery on failure
- Provides status updates without blocking
- Handles server reboots gracefully
"""

import time
import threading
from typing import Optional, Callable, TYPE_CHECKING
from dataclasses import dataclass
from enum import Enum

if TYPE_CHECKING:
    from remote_server import RemoteGateway, ServiceStatus
else:
    # Import at runtime to avoid circular import
    RemoteGateway = None
    ServiceStatus = None


class HeartbeatState(Enum):
    """Heartbeat monitor state."""
    RUNNING = "running"
    STOPPED = "stopped"
    RECOVERING = "recovering"


@dataclass
class HeartbeatConfig:
    """Heartbeat monitor configuration."""
    check_interval: int = 10  # seconds between checks
    timeout: int = 5  # seconds for each check
    max_retries: int = 3  # retries before declaring unhealthy
    recovery_interval: int = 30  # seconds between recovery attempts
    reboot_wait_time: int = 120  # seconds to wait after detecting reboot


class HeartbeatMonitor:
    """Background heartbeat monitor for remote servers.

    Usage:
        monitor = HeartbeatMonitor(gateway, on_status_change=callback)
        monitor.start()
        # ... do other work ...
        monitor.stop()
    """

    def __init__(
        self,
        gateway: "RemoteGateway",
        config: Optional[HeartbeatConfig] = None,
        on_status_change: Optional[Callable[["ServiceStatus", str], None]] = None
    ):
        """Initialize heartbeat monitor.

        Args:
            gateway: RemoteGateway instance to monitor
            config: Optional configuration
            on_status_change: Callback(status, message) when status changes
        """
        # Import here to avoid circular import
        from remote_server import ServiceStatus as SS
        global ServiceStatus
        ServiceStatus = SS

        self.gateway = gateway
        self.config = config or HeartbeatConfig()
        self.on_status_change = on_status_change

        self._state = HeartbeatState.STOPPED
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_status = ServiceStatus.UNKNOWN
        self._consecutive_failures = 0
        self._is_rebooting = False
        self._reboot_detected_at: Optional[float] = None

    def start(self):
        """Start heartbeat monitoring in background thread."""
        if self._state == HeartbeatState.RUNNING:
            return

        self._stop_event.clear()
        self._state = HeartbeatState.RUNNING
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

        self._notify_status_change(ServiceStatus.UNKNOWN, "Heartbeat monitor started")

    def stop(self):
        """Stop heartbeat monitoring."""
        if self._state == HeartbeatState.STOPPED:
            return

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

        self._state = HeartbeatState.STOPPED
        self._notify_status_change(ServiceStatus.UNKNOWN, "Heartbeat monitor stopped")

    def get_status(self) -> dict:
        """Get current monitor status.

        Returns:
            Dict with monitor state and server status
        """
        return {
            "monitor_state": self._state.value,
            "server_status": self._last_status.value,
            "consecutive_failures": self._consecutive_failures,
            "is_rebooting": self._is_rebooting,
            "profile": self.gateway.profile.name,
            "ssh_target": self.gateway.profile.ssh_target,
        }

    def _monitor_loop(self):
        """Main monitoring loop (runs in background thread)."""
        while not self._stop_event.is_set():
            try:
                # Check if we're waiting for reboot
                if self._is_rebooting:
                    self._handle_reboot_wait()
                else:
                    self._perform_health_check()

                # Wait for next check
                self._stop_event.wait(self.config.check_interval)

            except Exception as e:
                # Don't let exceptions kill the monitor thread
                self._notify_status_change(
                    ServiceStatus.UNKNOWN,
                    f"Monitor error: {str(e)}"
                )
                self._stop_event.wait(self.config.check_interval)

    def _perform_health_check(self):
        """Perform a single health check."""
        report = self.gateway.check_health(force=True)
        current_status = report.ssh_status

        # Detect status change
        if current_status != self._last_status:
            self._on_status_transition(self._last_status, current_status, report)
            self._last_status = current_status

        # Handle failures
        if current_status != ServiceStatus.HEALTHY:
            self._consecutive_failures += 1

            if self._consecutive_failures >= self.config.max_retries:
                self._handle_persistent_failure(report)
        else:
            # Reset failure counter on success
            if self._consecutive_failures > 0:
                self._notify_status_change(
                    ServiceStatus.HEALTHY,
                    "Server recovered"
                )
            self._consecutive_failures = 0

    def _on_status_transition(self, old_status, new_status, report):
        """Handle status transition."""
        if old_status == ServiceStatus.HEALTHY and new_status != ServiceStatus.HEALTHY:
            # Server became unhealthy
            self._notify_status_change(
                new_status,
                f"Server became unhealthy: {report.details.get('ssh', 'Unknown')}"
            )

        elif old_status != ServiceStatus.HEALTHY and new_status == ServiceStatus.HEALTHY:
            # Server recovered
            self._notify_status_change(
                ServiceStatus.HEALTHY,
                "Server recovered and is now healthy"
            )
            self._is_rebooting = False
            self._reboot_detected_at = None

    def _handle_persistent_failure(self, report):
        """Handle persistent connection failure."""
        if self._state == HeartbeatState.RECOVERING:
            return  # Already recovering

        self._state = HeartbeatState.RECOVERING

        # Check if this might be a reboot
        ssh_detail = report.details.get('ssh', '')
        if 'Connection refused' in ssh_detail or 'Timeout' in ssh_detail:
            if not self._is_rebooting:
                self._is_rebooting = True
                self._reboot_detected_at = time.time()
                self._notify_status_change(
                    ServiceStatus.UNHEALTHY,
                    f"Server appears to be rebooting (will wait {self.config.reboot_wait_time}s)"
                )
        else:
            self._notify_status_change(
                ServiceStatus.UNHEALTHY,
                f"Persistent failure: {ssh_detail}"
            )

    def _handle_reboot_wait(self):
        """Handle waiting for server reboot."""
        if not self._reboot_detected_at:
            return

        elapsed = time.time() - self._reboot_detected_at
        remaining = self.config.reboot_wait_time - elapsed

        if remaining > 0:
            # Still waiting
            self._notify_status_change(
                ServiceStatus.UNHEALTHY,
                f"Waiting for reboot to complete ({int(remaining)}s remaining)"
            )
        else:
            # Try to reconnect
            report = self.gateway.check_health(force=True)
            if report.ssh_status == ServiceStatus.HEALTHY:
                self._is_rebooting = False
                self._reboot_detected_at = None
                self._consecutive_failures = 0
                self._state = HeartbeatState.RUNNING
                self._notify_status_change(
                    ServiceStatus.HEALTHY,
                    "Server reboot completed successfully"
                )
            else:
                # Still not up, extend wait time
                self._reboot_detected_at = time.time()
                self._notify_status_change(
                    ServiceStatus.UNHEALTHY,
                    f"Server not yet responsive, extending wait time"
                )

    def _notify_status_change(self, status: ServiceStatus, message: str):
        """Notify callback of status change."""
        if self.on_status_change:
            try:
                self.on_status_change(status, message)
            except Exception:
                # Don't let callback exceptions break monitoring
                pass


def monitor_server_with_heartbeat(
    profile_name: str,
    check_interval: int = 10,
    verbose: bool = True
) -> HeartbeatMonitor:
    """Convenience function to start monitoring a server.

    Args:
        profile_name: Profile name to monitor
        check_interval: Seconds between checks
        verbose: Print status updates

    Returns:
        HeartbeatMonitor instance (already started)

    Example:
        monitor = monitor_server_with_heartbeat("tsinghua")
        # ... do other work ...
        monitor.stop()
    """
    from remote_server import RemoteGateway, ServiceStatus

    gateway = RemoteGateway(profile_name)

    def status_callback(status: ServiceStatus, message: str):
        if verbose:
            timestamp = time.strftime("%H:%M:%S")
            icon = {
                ServiceStatus.HEALTHY: "✓",
                ServiceStatus.DEGRADED: "⚠",
                ServiceStatus.UNHEALTHY: "✗",
                ServiceStatus.UNKNOWN: "?",
            }.get(status, "?")
            print(f"[{timestamp}] {icon} {message}")

    config = HeartbeatConfig(check_interval=check_interval)
    monitor = HeartbeatMonitor(gateway, config, on_status_change=status_callback)
    monitor.start()

    return monitor
