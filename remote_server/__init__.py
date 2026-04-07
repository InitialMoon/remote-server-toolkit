"""Remote Server Gateway - Unified entry point for remote server management.

This module keeps the legacy health-oriented API surface for compatibility,
but it now derives that compatibility layer from the explicit connectivity and
orchestration state machines defined in ``remote_server.state_machine``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import subprocess
import time
from typing import Any

from remote_bmc import BMCController, load_bmc_config
from remote_tmux.config import RemoteProfile, load_remote_profiles
from remote_tmux.manager import RemoteTmuxManager


class ServiceStatus(Enum):
    """Legacy service health status used by the heartbeat monitor."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConnectivityReport:
    """Terminal result of one connectivity-machine run."""

    profile_name: str
    ssh_target: str
    state: "ConnectivityStateId"
    reason: str
    details: dict[str, Any]
    history: tuple[str, ...]
    timestamp: float


@dataclass(frozen=True)
class OrchestrationReport:
    """Top-level remote readiness summary derived from layered machines."""

    profile_name: str
    ssh_target: str
    orchestration_state: "OrchestrationStateId"
    connectivity_state: "ConnectivityStateId"
    tmux_state: "TmuxStateId | None"
    task_state: "TaskStateId | None"
    reason: str
    details: dict[str, Any]
    history: tuple[str, ...]
    timestamp: float


@dataclass
class HealthReport:
    """Compatibility health report derived from orchestration semantics."""

    ssh_status: ServiceStatus
    tmux_status: ServiceStatus
    bmc_status: ServiceStatus
    server_responsive: bool
    details: dict[str, Any]
    timestamp: float


from remote_server.state_machine import (
    ConnectivityContext,
    ConnectivityControlAdapter,
    ConnectivitySnapshot,
    ConnectivityStateId,
    ConnectivityStateMachine,
    ConnectivityTransitionResult,
    DEFAULT_CONNECTIVITY_STATE_SPECS,
    DEFAULT_ORCHESTRATION_STATE_SPECS,
    DEFAULT_TASK_STATE_SPECS,
    DEFAULT_TMUX_STATE_SPECS,
    OrchestrationContext,
    OrchestrationStateId,
    OrchestrationTransitionResult,
    RemoteOrchestrationMachine,
    StateKind,
    StateSpec,
    TaskContext,
    TaskControlAdapter,
    TaskSnapshot,
    TaskStateId,
    TaskStateMachine,
    TaskTransitionResult,
    TmuxContext,
    TmuxControlAdapter,
    TmuxSnapshot,
    TmuxStateId,
    TmuxStateMachine,
    TmuxTransitionResult,
)

# Import heartbeat after defining ServiceStatus / HealthReport / RemoteGateway-compatible
# dataclasses to avoid circular import issues.
from remote_server.heartbeat import (
    HeartbeatConfig,
    HeartbeatEvent,
    HeartbeatEventKind,
    HeartbeatMonitor,
    HeartbeatState,
    RecoveryState,
    monitor_server_with_heartbeat,
)

__all__ = [
    "ConnectivityReport",
    "ConnectivitySnapshot",
    "ConnectivityStateId",
    "ConnectivityStateMachine",
    "HeartbeatConfig",
    "HeartbeatEvent",
    "HeartbeatEventKind",
    "HeartbeatMonitor",
    "HeartbeatState",
    "HealthReport",
    "monitor_server_with_heartbeat",
    "OrchestrationReport",
    "OrchestrationStateId",
    "RemoteGateway",
    "RemoteOrchestrationMachine",
    "RecoveryState",
    "ServiceStatus",
    "TaskSnapshot",
    "TaskStateId",
    "TaskStateMachine",
    "TmuxSnapshot",
    "TmuxStateId",
    "TmuxStateMachine",
]


class RemoteGateway(ConnectivityControlAdapter, TmuxControlAdapter, TaskControlAdapter):
    """Unified gateway that adapts the toolkit's explicit state machines."""

    _CONNECTIVITY_MAX_STEPS = 8
    _TMUX_MAX_STEPS = 8
    _TASK_MAX_STEPS = 8

    def __init__(self, profile_name: str, config_root: str | Path | None = None):
        if config_root is None:
            user_config = Path.home() / ".config"
            config_path = user_config if (user_config / "remote_tmux").exists() else Path.cwd()
        else:
            config_path = Path(config_root)

        profiles = load_remote_profiles(config_path)
        if profile_name not in profiles:
            raise ValueError(f"Profile '{profile_name}' not found")

        self.profile = profiles[profile_name]
        self.tmux = RemoteTmuxManager()
        self.bmc = None
        bmc_config = load_bmc_config(config_path)
        if bmc_config:
            self.bmc = BMCController(bmc_config)

        self._last_health_check: HealthReport | None = None
        self._health_check_interval = 60
        self._tmux_window_name: str | None = None
        self._task_window_name: str | None = None
        self._last_connectivity_details: dict[str, Any] = {}
        self._last_tmux_details: dict[str, Any] = {}
        self._last_task_details: dict[str, Any] = {}

    def check_health(self, force: bool = False) -> HealthReport:
        """Return a compatibility health report built from orchestration state."""
        now = time.time()
        if not force and self._last_health_check is not None:
            age = now - self._last_health_check.timestamp
            if age < self._health_check_interval:
                return self._last_health_check

        report = self.get_orchestration_report(auto_recover=False, auto_create=False)
        details = dict(report.details)
        details["connectivity_state"] = report.connectivity_state.value
        details["orchestration_state"] = report.orchestration_state.value
        details["reason"] = report.reason
        if report.tmux_state is not None:
            details["tmux_state"] = report.tmux_state.value
        if report.task_state is not None:
            details["task_state"] = report.task_state.value

        health = HealthReport(
            ssh_status=self._map_connectivity_to_service_status(report.connectivity_state),
            tmux_status=self._map_tmux_to_service_status(report.tmux_state),
            bmc_status=self._map_bmc_to_service_status(report.details.get("bmc_ok")),
            server_responsive=report.connectivity_state == ConnectivityStateId.READY,
            details=details,
            timestamp=report.timestamp,
        )
        self._last_health_check = health
        return health

    def ensure_healthy(self, auto_recover: bool = True) -> bool:
        """Ensure connectivity and tmux session readiness for remote control."""
        report = self.get_orchestration_report(auto_recover=auto_recover, auto_create=auto_recover)
        return report.orchestration_state == OrchestrationStateId.INSPECTING_TASK

    def get_connectivity_report(self, auto_recover: bool = False) -> ConnectivityReport:
        """Run the simplified connectivity machine to a terminal state."""
        self._last_connectivity_details = {}
        machine = ConnectivityStateMachine(adapter=self, auto_recover=auto_recover)
        transition = self._step_connectivity_machine(machine)
        reason = transition.reason if transition is not None else "Connectivity machine did not run."
        return ConnectivityReport(
            profile_name=self.profile.name,
            ssh_target=self.profile.ssh_target,
            state=machine.current_state,
            reason=reason,
            details=dict(self._last_connectivity_details),
            history=tuple(machine.context.history),
            timestamp=time.time(),
        )

    def get_orchestration_report(
        self,
        *,
        auto_recover: bool = False,
        auto_create: bool = False,
        task_name: str | None = None,
    ) -> OrchestrationReport:
        """Evaluate layered readiness for external callers.

        Without ``task_name`` this method stops once connectivity and tmux are
        both ready, and reports ``INSPECTING_TASK`` as the top-level state to
        indicate that the remote infrastructure is ready for task-level work.
        """
        connectivity = self.get_connectivity_report(auto_recover=auto_recover)
        history = list(connectivity.history)
        if connectivity.state != ConnectivityStateId.READY:
            return OrchestrationReport(
                profile_name=self.profile.name,
                ssh_target=self.profile.ssh_target,
                orchestration_state=OrchestrationStateId.BLOCKED_CONNECTIVITY,
                connectivity_state=connectivity.state,
                tmux_state=None,
                task_state=None,
                reason=connectivity.reason,
                details=dict(connectivity.details),
                history=tuple(history),
                timestamp=time.time(),
            )

        self._tmux_window_name = task_name
        self._last_tmux_details = {}
        tmux_machine = TmuxStateMachine(adapter=self, auto_create=auto_create)
        tmux_transition = self._step_tmux_machine(tmux_machine)
        history.extend(tmux_machine.context.history)
        details = dict(connectivity.details)
        details.update(self._last_tmux_details)
        reason = tmux_transition.reason if tmux_transition is not None else "Tmux machine did not run."

        if tmux_machine.current_state != TmuxStateId.WINDOW_READY:
            return OrchestrationReport(
                profile_name=self.profile.name,
                ssh_target=self.profile.ssh_target,
                orchestration_state=OrchestrationStateId.BLOCKED_TMUX,
                connectivity_state=connectivity.state,
                tmux_state=tmux_machine.current_state,
                task_state=None,
                reason=reason,
                details=details,
                history=tuple(history),
                timestamp=time.time(),
            )

        if task_name is None:
            return OrchestrationReport(
                profile_name=self.profile.name,
                ssh_target=self.profile.ssh_target,
                orchestration_state=OrchestrationStateId.INSPECTING_TASK,
                connectivity_state=connectivity.state,
                tmux_state=tmux_machine.current_state,
                task_state=None,
                reason="Connectivity and tmux are ready; task inspection not requested.",
                details=details,
                history=tuple(history),
                timestamp=time.time(),
            )

        self._task_window_name = task_name
        self._last_task_details = {}
        task_machine = TaskStateMachine(adapter=self)
        task_transition = self._step_task_machine(task_machine)
        history.extend(task_machine.context.history)
        details.update(self._last_task_details)
        task_reason = task_transition.reason if task_transition is not None else "Task machine did not run."
        orchestration_state = {
            TaskStateId.IDLE: OrchestrationStateId.TASK_IDLE,
            TaskStateId.RUNNING: OrchestrationStateId.TASK_RUNNING,
            TaskStateId.SUCCEEDED: OrchestrationStateId.TASK_SUCCEEDED,
            TaskStateId.FAILED: OrchestrationStateId.TASK_FAILED,
        }[task_machine.current_state]
        return OrchestrationReport(
            profile_name=self.profile.name,
            ssh_target=self.profile.ssh_target,
            orchestration_state=orchestration_state,
            connectivity_state=connectivity.state,
            tmux_state=tmux_machine.current_state,
            task_state=task_machine.current_state,
            reason=task_reason,
            details=details,
            history=tuple(history),
            timestamp=time.time(),
        )

    def get_status_summary(self) -> str:
        """Render the current remote semantics in a concise human-readable form."""
        report = self.get_orchestration_report(auto_recover=False, auto_create=False)
        lines = [
            f"Profile: {report.profile_name}",
            f"Target: {report.ssh_target}",
            "",
            f"Connectivity state: {report.connectivity_state.value}",
            f"Orchestration state: {report.orchestration_state.value}",
        ]
        if report.tmux_state is not None:
            lines.append(f"Tmux state: {report.tmux_state.value}")
        if report.task_state is not None:
            lines.append(f"Task state: {report.task_state.value}")
        lines.extend(
            [
                f"Reason: {report.reason}",
                "",
                f"BMC reachable: {self._format_bool(report.details.get('bmc_ok'))}",
                f"Host powered on: {self._format_bool(report.details.get('host_powered_on'))}",
                f"SSH reachable: {self._format_bool(report.details.get('ssh_ok'))}",
            ]
        )
        return "\n".join(lines)

    def probe_connectivity(self) -> ConnectivitySnapshot:
        """Collect the simplified connectivity evidence used by the state machine."""
        snapshot = self._probe_connectivity_snapshot()
        return snapshot

    def recover_host_power(self) -> ConnectivitySnapshot | None:
        """Perform a bounded host power recovery, then return fresh evidence."""
        if self.bmc is None:
            self._last_connectivity_details = {
                "bmc_ok": None,
                "host_powered_on": None,
                "ssh_ok": None,
                "recovery_action": "power_on",
                "recovery_error": "BMC is not configured.",
            }
            return None

        if not self.bmc.power_on():
            self._last_connectivity_details = {
                "bmc_ok": True,
                "host_powered_on": False,
                "ssh_ok": None,
                "recovery_action": "power_on",
                "recovery_error": "BMC power on request failed.",
            }
            return None

        return self._poll_connectivity_snapshot(
            action_name="power_on",
            stop_when=lambda snapshot: snapshot.bmc_ok is True and snapshot.host_powered_on is True,
        )

    def recover_ssh(self) -> ConnectivitySnapshot | None:
        """Perform a bounded SSH recovery action, then return fresh evidence."""
        if self.bmc is None:
            self._last_connectivity_details = {
                "bmc_ok": None,
                "host_powered_on": None,
                "ssh_ok": None,
                "recovery_action": "reset",
                "recovery_error": "BMC is not configured.",
            }
            return None

        if not self.bmc.reset():
            self._last_connectivity_details = {
                "bmc_ok": True,
                "host_powered_on": True,
                "ssh_ok": False,
                "recovery_action": "reset",
                "recovery_error": "BMC reset request failed.",
            }
            return None

        time.sleep(self.profile.bmc_reset_wait_seconds)
        return self._poll_connectivity_snapshot(
            action_name="reset",
            stop_when=lambda snapshot: (
                snapshot.bmc_ok is True
                and snapshot.host_powered_on is True
                and snapshot.ssh_ok is True
            ),
        )

    def probe_tmux(self) -> TmuxSnapshot:
        """Collect tmux session/window evidence for the current orchestration target."""
        ssh_ok, ssh_detail = self._probe_ssh_signal()
        details: dict[str, Any] = {
            "ssh_ok": ssh_ok,
            "ssh_detail": ssh_detail,
            "tmux_window_target": self._tmux_window_name,
        }
        if ssh_ok is not True:
            details.update(
                {
                    "session_exists": None,
                    "session_healthy": None,
                    "window_exists": None,
                }
            )
            self._last_tmux_details = details
            return TmuxSnapshot(
                ssh_ok=ssh_ok,
                session_exists=None,
                session_healthy=None,
                window_exists=None,
            )

        session_exists = self._tmux_session_exists()
        session_healthy = session_exists
        window_exists = None
        if session_exists:
            window_exists = True if self._tmux_window_name is None else self._tmux_window_exists(
                self._tmux_window_name
            )

        details.update(
            {
                "session_exists": session_exists,
                "session_healthy": session_healthy,
                "window_exists": window_exists,
            }
        )
        self._last_tmux_details = details
        return TmuxSnapshot(
            ssh_ok=ssh_ok,
            session_exists=session_exists,
            session_healthy=session_healthy,
            window_exists=window_exists,
        )

    def ensure_session(self) -> bool:
        """Create the managed tmux session if it does not exist."""
        script = f"""
set -e
SESSION="{self.profile.session_name}"
REPO="{self.profile.repo_path}"
PROFILE="{self.profile.name}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    exit 0
fi

tmux new-session -d -s "$SESSION" -n home -c "$REPO"
tmux set-option -t "$SESSION" -q @chrono_managed 1
tmux set-option -t "$SESSION" -q @chrono_profile "$PROFILE"
tmux set-option -t "$SESSION" -gq window-size latest
"""
        result = self._run_remote_script(script)
        return result.returncode == 0

    def ensure_window(self) -> bool:
        """Create the target tmux window if the orchestration requests one."""
        if self._tmux_window_name is None:
            return True

        self.tmux.validate_task_name(self._tmux_window_name)
        script = f"""
set -e
SESSION="{self.profile.session_name}"
TASK="{self._tmux_window_name}"
REPO="{self.profile.repo_path}"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    exit 1
fi

if tmux list-windows -t "$SESSION" -F "#W" | grep -qx "$TASK"; then
    exit 0
fi

tmux new-window -t "$SESSION" -n "$TASK" -c "$REPO"
"""
        result = self._run_remote_script(script)
        return result.returncode == 0

    def inspect_task(self) -> TaskSnapshot:
        """Inspect one managed task window.

        The richer task lifecycle remains optional for callers. Status/ensure
        stop before this layer unless a concrete ``task_name`` is requested.
        """
        if self._task_window_name is None:
            raise RuntimeError("Task inspection requested without a task window name.")

        script = f"""
SESSION="{self.profile.session_name}"
TASK="{self._task_window_name}"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "window_exists=false"
    exit 0
fi

if ! tmux list-windows -t "$SESSION" -F "#W" | grep -qx "$TASK"; then
    echo "window_exists=false"
    exit 0
fi

tmux list-panes -t "$SESSION:$TASK" -F "window_exists=true pane_active=#{{pane_active}} pane_dead=#{{pane_dead}} pane_dead_status=#{{pane_dead_status}} pane_current_command=#{{pane_current_command}}" | awk '$2 == "pane_active=1" {{print; exit}}'
"""
        result = self._run_remote_script(script)
        snapshot = self._parse_task_snapshot(result.stdout)
        self._last_task_details = {
            "task_window": self._task_window_name,
            "task_stdout": result.stdout.strip(),
            "task_stderr": result.stderr.strip(),
            "window_exists": snapshot.window_exists,
            "command_active": snapshot.command_active,
            "exit_code": snapshot.exit_code,
        }
        return snapshot

    def _step_connectivity_machine(
        self, machine: ConnectivityStateMachine
    ) -> ConnectivityTransitionResult | None:
        transition = None
        for _ in range(self._CONNECTIVITY_MAX_STEPS):
            transition = machine.step()
            if machine.is_terminal():
                return transition
        machine.context.last_error = "Connectivity machine exceeded the bounded step budget."
        machine.current_state = ConnectivityStateId.FAILED
        machine.context.history.append("bounded_step_budget_exceeded")
        return ConnectivityTransitionResult(
            from_state=transition.to_state if transition is not None else ConnectivityStateId.UNKNOWN,
            to_state=ConnectivityStateId.FAILED,
            action="fail",
            reason="Connectivity machine exceeded the bounded step budget.",
            context=machine.context,
        )

    def _step_tmux_machine(self, machine: TmuxStateMachine) -> TmuxTransitionResult | None:
        transition = None
        for _ in range(self._TMUX_MAX_STEPS):
            transition = machine.step()
            if machine.is_terminal():
                return transition
        machine.context.last_error = "Tmux machine exceeded the bounded step budget."
        machine.current_state = TmuxStateId.FAILED
        machine.context.history.append("bounded_step_budget_exceeded")
        return TmuxTransitionResult(
            from_state=transition.to_state if transition is not None else TmuxStateId.UNKNOWN,
            to_state=TmuxStateId.FAILED,
            action="fail",
            reason="Tmux machine exceeded the bounded step budget.",
            context=machine.context,
        )

    def _step_task_machine(self, machine: TaskStateMachine) -> TaskTransitionResult | None:
        transition = None
        for _ in range(self._TASK_MAX_STEPS):
            transition = machine.step()
            if machine.is_terminal():
                return transition
        machine.context.last_error = "Task machine exceeded the bounded step budget."
        machine.current_state = TaskStateId.FAILED
        machine.context.history.append("bounded_step_budget_exceeded")
        return TaskTransitionResult(
            from_state=transition.to_state if transition is not None else TaskStateId.UNKNOWN,
            to_state=TaskStateId.FAILED,
            action="fail",
            reason="Task machine exceeded the bounded step budget.",
            context=machine.context,
        )

    def _probe_connectivity_snapshot(self) -> ConnectivitySnapshot:
        bmc_ok, host_powered_on, bmc_detail = self._probe_bmc_signal()
        ssh_ok = None
        ssh_detail = "Skipped because BMC or host power state did not allow SSH probing."
        if bmc_ok is True and host_powered_on is True:
            ssh_ok, ssh_detail = self._probe_ssh_signal()

        self._last_connectivity_details = {
            "bmc_ok": bmc_ok,
            "host_powered_on": host_powered_on,
            "ssh_ok": ssh_ok,
            "bmc_detail": bmc_detail,
            "ssh_detail": ssh_detail,
        }
        return ConnectivitySnapshot(
            ssh_ok=ssh_ok,
            bmc_ok=bmc_ok,
            host_powered_on=host_powered_on,
        )

    def _probe_bmc_signal(self) -> tuple[bool | None, bool | None, str]:
        if self.bmc is None:
            return None, None, "BMC is not configured."
        try:
            status = self.bmc.status()
        except Exception as exc:
            return None, None, f"BMC status probe failed: {exc}"

        if status.get("reachable") != "true":
            return False, None, status.get("error", "BMC is unreachable.")

        power_state = status.get("System Power", "unknown").strip().lower()
        if power_state == "on":
            return True, True, "BMC reachable and host powered on."
        if power_state == "off":
            return True, False, "BMC reachable and host powered off."
        return True, None, f"BMC reachable but power state is '{power_state}'."

    def _probe_ssh_signal(self) -> tuple[bool | None, str]:
        cmd = [
            "ssh",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            self.profile.ssh_target,
            "echo ok",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.profile.ssh_probe_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return False, "SSH probe timed out."
        except Exception as exc:
            return None, f"SSH probe failed: {exc}"

        if result.returncode == 0:
            return True, "SSH probe succeeded."
        stderr = result.stderr.strip() or "SSH probe failed."
        return False, stderr

    def _poll_connectivity_snapshot(
        self,
        *,
        action_name: str,
        stop_when,
    ) -> ConnectivitySnapshot | None:
        last_snapshot = None
        for attempt in range(1, self.profile.ssh_recovery_attempts + 1):
            snapshot = self._probe_connectivity_snapshot()
            self._last_connectivity_details["recovery_action"] = action_name
            self._last_connectivity_details["recovery_attempt"] = attempt
            last_snapshot = snapshot
            if stop_when(snapshot):
                return snapshot
            if attempt < self.profile.ssh_recovery_attempts:
                time.sleep(self.profile.ssh_recovery_interval_seconds)

        if last_snapshot is not None:
            self._last_connectivity_details["recovery_error"] = (
                f"Timed out waiting for recovery action '{action_name}'."
            )
        return None

    def _tmux_session_exists(self) -> bool:
        script = f'tmux has-session -t "{self.profile.session_name}" 2>/dev/null'
        result = self._run_remote_script(script)
        return result.returncode == 0

    def _tmux_window_exists(self, window_name: str) -> bool:
        script = f"""
SESSION="{self.profile.session_name}"
TASK="{window_name}"
tmux list-windows -t "$SESSION" -F "#W" | grep -qx "$TASK"
"""
        result = self._run_remote_script(script)
        return result.returncode == 0

    def _run_remote_script(self, script: str) -> subprocess.CompletedProcess[str]:
        command = self.tmux._build_ssh_command(self.profile, script)
        return self.tmux.execute(command, check=False)

    def _parse_task_snapshot(self, stdout: str) -> TaskSnapshot:
        tokens: dict[str, str] = {}
        for token in stdout.strip().split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            tokens[key] = value

        if tokens.get("window_exists") != "true":
            return TaskSnapshot(window_exists=False, command_active=None, exit_code=None)

        pane_dead = tokens.get("pane_dead")
        pane_dead_status = tokens.get("pane_dead_status")
        current_command = tokens.get("pane_current_command", "")
        command_active = current_command not in {"", "bash", "zsh", "sh", "fish"}
        if pane_dead == "1":
            exit_code = int(pane_dead_status) if pane_dead_status is not None else None
            return TaskSnapshot(window_exists=True, command_active=False, exit_code=exit_code)
        return TaskSnapshot(window_exists=True, command_active=command_active, exit_code=None)

    def _map_connectivity_to_service_status(
        self, state: ConnectivityStateId
    ) -> ServiceStatus:
        if state == ConnectivityStateId.READY:
            return ServiceStatus.HEALTHY
        if state == ConnectivityStateId.FAILED:
            return ServiceStatus.UNKNOWN
        return ServiceStatus.UNHEALTHY

    def _map_tmux_to_service_status(self, state: TmuxStateId | None) -> ServiceStatus:
        if state == TmuxStateId.WINDOW_READY:
            return ServiceStatus.HEALTHY
        if state in {TmuxStateId.SESSION_MISSING, TmuxStateId.WINDOW_MISSING}:
            return ServiceStatus.DEGRADED
        if state in {TmuxStateId.DEGRADED, TmuxStateId.FAILED}:
            return ServiceStatus.UNHEALTHY
        return ServiceStatus.UNKNOWN

    def _map_bmc_to_service_status(self, bmc_ok: Any) -> ServiceStatus:
        if bmc_ok is True:
            return ServiceStatus.HEALTHY
        if bmc_ok is False:
            return ServiceStatus.UNHEALTHY
        return ServiceStatus.UNKNOWN

    def _format_bool(self, value: Any) -> str:
        if value is True:
            return "true"
        if value is False:
            return "false"
        return "unknown"
