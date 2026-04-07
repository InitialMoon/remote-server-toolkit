"""Structured heartbeat monitoring for remote orchestration.

This module no longer treats "healthy/unhealthy" as the primary signal.
Instead, it consumes orchestration reports and emits sparse, structured events
that tell callers where progress is blocked and whether bounded recovery
attempts helped.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

from remote_server.state_machine import (
    ConnectivityStateId,
    OrchestrationStateId,
    TaskStateId,
    TmuxStateId,
)

if TYPE_CHECKING:
    from remote_server import OrchestrationReport, RemoteGateway, ServiceStatus
else:
    OrchestrationReport = None
    RemoteGateway = None
    ServiceStatus = None


class HeartbeatState(Enum):
    """Heartbeat monitor state."""

    RUNNING = "running"
    STOPPED = "stopped"


class RecoveryState(Enum):
    """Current recovery status attached to emitted events."""

    IDLE = "idle"
    INFLIGHT = "inflight"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class HeartbeatEventKind(Enum):
    """Stable event kinds emitted by the orchestration heartbeat."""

    LIFECYCLE = "lifecycle"
    STATE = "state"
    BLOCKED = "blocked"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_SUCCEEDED = "recovery_succeeded"
    RECOVERY_FAILED = "recovery_failed"


@dataclass(frozen=True)
class HeartbeatEvent:
    """Structured event emitted by ``HeartbeatMonitor``."""

    profile: str
    kind: HeartbeatEventKind
    connectivity_state: ConnectivityStateId
    orchestration_state: OrchestrationStateId
    tmux_state: TmuxStateId | None
    task_state: TaskStateId | None
    unchanged_count: int
    recovery_state: RecoveryState
    recovery_reason: str | None
    message: str
    timestamp: float


@dataclass
class HeartbeatConfig:
    """Heartbeat monitor configuration."""

    check_interval: int = 10
    timeout: int = 5
    max_retries: int = 3
    recovery_interval: int = 30


class HeartbeatMonitor:
    """Background orchestration monitor for remote servers."""

    def __init__(
        self,
        gateway: "RemoteGateway",
        config: HeartbeatConfig | None = None,
        on_event: Callable[[HeartbeatEvent], None] | None = None,
        on_status_change: Callable[["ServiceStatus", str], None] | None = None,
    ) -> None:
        from remote_server import ServiceStatus as SS

        global ServiceStatus
        ServiceStatus = SS

        self.gateway = gateway
        self.config = config or HeartbeatConfig()
        self.on_event = on_event
        self.on_status_change = on_status_change

        self._state = HeartbeatState.STOPPED
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._last_connectivity_state: ConnectivityStateId | None = None
        self._last_orchestration_state: OrchestrationStateId | None = None
        self._last_tmux_state: TmuxStateId | None = None
        self._last_task_state: TaskStateId | None = None
        self._last_report_time: float | None = None
        self._unchanged_count = 0
        self._recovery_inflight = False
        self._last_recovery_reason: str | None = None
        self._last_recovery_time: float | None = None

    def start(self) -> None:
        """Start heartbeat monitoring in a background thread."""
        if self._state == HeartbeatState.RUNNING:
            return

        self._stop_event.clear()
        self._state = HeartbeatState.RUNNING
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        self._emit_lifecycle_event("monitor started")

    def stop(self) -> None:
        """Stop heartbeat monitoring."""
        if self._state == HeartbeatState.STOPPED:
            return

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._state = HeartbeatState.STOPPED
        self._emit_lifecycle_event("monitor stopped")

    def check_once(self) -> None:
        """Run one orchestration polling cycle and emit any resulting events."""
        report = self.gateway.get_orchestration_report(auto_recover=False, auto_create=False)
        self._handle_report(report)

    def get_status(self) -> dict[str, str | int | bool | None]:
        """Get current monitor status."""
        return {
            "monitor_state": self._state.value,
            "profile": self.gateway.profile.name,
            "ssh_target": self.gateway.profile.ssh_target,
            "connectivity_state": self._enum_value(self._last_connectivity_state),
            "orchestration_state": self._enum_value(self._last_orchestration_state),
            "tmux_state": self._enum_value(self._last_tmux_state),
            "task_state": self._enum_value(self._last_task_state),
            "unchanged_count": self._unchanged_count,
            "recovery_inflight": self._recovery_inflight,
            "last_recovery_reason": self._last_recovery_reason,
            "last_report_time": self._last_report_time,
        }

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception as exc:
                self._emit_lifecycle_event(f"monitor error: {exc}")
            self._stop_event.wait(self.config.check_interval)

    def _handle_report(self, report: "OrchestrationReport") -> None:
        state_changed = self._has_state_changed(report)

        if state_changed:
            self._emit_report_event(
                kind=HeartbeatEventKind.STATE,
                report=report,
                unchanged_count=0,
                recovery_state=RecoveryState.IDLE,
                recovery_reason=None,
                message=report.reason or self._describe_report(report),
            )
            self._unchanged_count = 1
        else:
            self._unchanged_count += 1

        self._store_report_state(report)

        if not self._is_blocked(report):
            self._recovery_inflight = False
            return

        if self._should_emit_blocked_event():
            self._emit_report_event(
                kind=HeartbeatEventKind.BLOCKED,
                report=report,
                unchanged_count=self._unchanged_count,
                recovery_state=RecoveryState.IDLE,
                recovery_reason=self._recovery_reason_for(report),
                message=report.reason or self._describe_report(report),
            )

        if self._should_attempt_recovery(report):
            self._attempt_recovery(report)

    def _attempt_recovery(self, report: "OrchestrationReport") -> None:
        recovery_reason = self._recovery_reason_for(report)
        recovery_kwargs = self._recovery_kwargs_for(report)
        if recovery_kwargs is None:
            return

        self._recovery_inflight = True
        self._last_recovery_reason = recovery_reason
        self._last_recovery_time = time.time()
        self._emit_report_event(
            kind=HeartbeatEventKind.RECOVERY_STARTED,
            report=report,
            unchanged_count=self._unchanged_count,
            recovery_state=RecoveryState.INFLIGHT,
            recovery_reason=recovery_reason,
            message=f"recovery started: reason={recovery_reason}",
        )

        recovery_report = self.gateway.get_orchestration_report(**recovery_kwargs)
        success = self._recovery_succeeded(before=report, after=recovery_report)
        recovery_state = RecoveryState.SUCCEEDED if success else RecoveryState.FAILED
        event_kind = (
            HeartbeatEventKind.RECOVERY_SUCCEEDED
            if success
            else HeartbeatEventKind.RECOVERY_FAILED
        )
        message = (
            f"recovery succeeded: reason={recovery_reason}"
            if success
            else f"recovery failed: reason={recovery_reason}"
        )
        self._emit_report_event(
            kind=event_kind,
            report=recovery_report,
            unchanged_count=0 if success else self._unchanged_count,
            recovery_state=recovery_state,
            recovery_reason=recovery_reason,
            message=message,
        )

        self._recovery_inflight = False
        self._store_report_state(recovery_report)
        self._unchanged_count = 1 if success else self._unchanged_count

    def _emit_lifecycle_event(self, message: str) -> None:
        connectivity_state = self._last_connectivity_state or ConnectivityStateId.UNKNOWN
        orchestration_state = self._last_orchestration_state or OrchestrationStateId.UNKNOWN
        event = HeartbeatEvent(
            profile=self.gateway.profile.name,
            kind=HeartbeatEventKind.LIFECYCLE,
            connectivity_state=connectivity_state,
            orchestration_state=orchestration_state,
            tmux_state=self._last_tmux_state,
            task_state=self._last_task_state,
            unchanged_count=self._unchanged_count,
            recovery_state=RecoveryState.IDLE,
            recovery_reason=self._last_recovery_reason,
            message=message,
            timestamp=time.time(),
        )
        self._emit_event(event)

    def _emit_report_event(
        self,
        *,
        kind: HeartbeatEventKind,
        report: "OrchestrationReport",
        unchanged_count: int,
        recovery_state: RecoveryState,
        recovery_reason: str | None,
        message: str,
    ) -> None:
        event = HeartbeatEvent(
            profile=report.profile_name,
            kind=kind,
            connectivity_state=report.connectivity_state,
            orchestration_state=report.orchestration_state,
            tmux_state=report.tmux_state,
            task_state=report.task_state,
            unchanged_count=unchanged_count,
            recovery_state=recovery_state,
            recovery_reason=recovery_reason,
            message=message,
            timestamp=report.timestamp,
        )
        self._emit_event(event)

    def _emit_event(self, event: HeartbeatEvent) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                pass

        if self.on_status_change is not None:
            try:
                self.on_status_change(self._legacy_status_for(event), event.message)
            except Exception:
                pass

    def _legacy_status_for(self, event: HeartbeatEvent) -> "ServiceStatus":
        if event.orchestration_state in {
            OrchestrationStateId.INSPECTING_TASK,
            OrchestrationStateId.TASK_IDLE,
            OrchestrationStateId.TASK_RUNNING,
            OrchestrationStateId.TASK_SUCCEEDED,
        }:
            return ServiceStatus.HEALTHY
        if event.orchestration_state == OrchestrationStateId.BLOCKED_TMUX:
            return ServiceStatus.DEGRADED
        if event.orchestration_state in {
            OrchestrationStateId.BLOCKED_CONNECTIVITY,
            OrchestrationStateId.TASK_FAILED,
        }:
            return ServiceStatus.UNHEALTHY
        return ServiceStatus.UNKNOWN

    def _has_state_changed(self, report: "OrchestrationReport") -> bool:
        return (
            report.connectivity_state != self._last_connectivity_state
            or report.orchestration_state != self._last_orchestration_state
            or report.tmux_state != self._last_tmux_state
            or report.task_state != self._last_task_state
        )

    def _store_report_state(self, report: "OrchestrationReport") -> None:
        self._last_connectivity_state = report.connectivity_state
        self._last_orchestration_state = report.orchestration_state
        self._last_tmux_state = report.tmux_state
        self._last_task_state = report.task_state
        self._last_report_time = report.timestamp

    def _is_blocked(self, report: "OrchestrationReport") -> bool:
        return report.orchestration_state in {
            OrchestrationStateId.BLOCKED_CONNECTIVITY,
            OrchestrationStateId.BLOCKED_TMUX,
        }

    def _should_emit_blocked_event(self) -> bool:
        threshold = max(1, self.config.max_retries)
        return self._unchanged_count >= threshold and self._unchanged_count % threshold == 0

    def _should_attempt_recovery(self, report: "OrchestrationReport") -> bool:
        if self._recovery_inflight:
            return False

        if self._recovery_kwargs_for(report) is None:
            return False

        if self._unchanged_count < max(1, self.config.max_retries):
            return False

        if self._last_recovery_time is None:
            return True

        return (time.time() - self._last_recovery_time) >= self.config.recovery_interval

    def _recovery_kwargs_for(
        self, report: "OrchestrationReport"
    ) -> dict[str, bool] | None:
        if report.orchestration_state == OrchestrationStateId.BLOCKED_CONNECTIVITY:
            return {"auto_recover": True, "auto_create": False}
        if report.orchestration_state == OrchestrationStateId.BLOCKED_TMUX:
            return {"auto_recover": False, "auto_create": True}
        return None

    def _recovery_reason_for(self, report: "OrchestrationReport") -> str:
        if report.orchestration_state == OrchestrationStateId.BLOCKED_CONNECTIVITY:
            return report.connectivity_state.value
        if report.orchestration_state == OrchestrationStateId.BLOCKED_TMUX:
            if report.tmux_state is not None:
                return report.tmux_state.value
            return report.orchestration_state.value
        return report.orchestration_state.value

    def _recovery_succeeded(
        self, *, before: "OrchestrationReport", after: "OrchestrationReport"
    ) -> bool:
        if before.orchestration_state == OrchestrationStateId.BLOCKED_CONNECTIVITY:
            return after.orchestration_state != OrchestrationStateId.BLOCKED_CONNECTIVITY
        if before.orchestration_state == OrchestrationStateId.BLOCKED_TMUX:
            return after.orchestration_state != OrchestrationStateId.BLOCKED_TMUX
        return False

    def _describe_report(self, report: "OrchestrationReport") -> str:
        parts = [
            f"connectivity={report.connectivity_state.value}",
            f"orchestration={report.orchestration_state.value}",
        ]
        if report.tmux_state is not None:
            parts.append(f"tmux={report.tmux_state.value}")
        if report.task_state is not None:
            parts.append(f"task={report.task_state.value}")
        if report.reason:
            parts.append(f"reason={report.reason}")
        return " ".join(parts)

    @staticmethod
    def _enum_value(value: Enum | None) -> str | None:
        return value.value if value is not None else None


def monitor_server_with_heartbeat(
    profile_name: str, check_interval: int = 10, verbose: bool = True
) -> HeartbeatMonitor:
    """Convenience function to start monitoring a server."""
    from remote_server import RemoteGateway

    gateway = RemoteGateway(profile_name)

    def event_callback(event: HeartbeatEvent) -> None:
        if not verbose:
            return
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {event.kind.value} {event.message}")

    monitor = HeartbeatMonitor(
        gateway,
        HeartbeatConfig(check_interval=check_interval),
        on_event=event_callback,
    )
    monitor.start()
    return monitor
