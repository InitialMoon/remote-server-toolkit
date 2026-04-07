"""Explicit remote state machines for reusable orchestration.

This module intentionally separates three different concerns:

1. connectivity: BMC/SSH reachability and host recovery
2. tmux: session/window availability once connectivity is ready
3. task: command execution state once a tmux window is available

The separation keeps each state graph small and readable. Higher layers can
compose them in order instead of auditing one mixed machine with unrelated
states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class StateKind(Enum):
    """Generic classification used by all machine-specific states."""

    OBSERVATION = "observation"
    ACTION = "action"
    FAULT = "fault"
    WAIT = "wait"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class StateSpec:
    """Static attributes for one node in one machine.

    Attributes:
        id: Stable enum value for this node. Callers log and compare this value
            instead of hard-coding string literals in orchestration code.
        layer: Ownership boundary for the state. This is intentionally one of
            `connectivity`, `tmux`, or `task`, so callers can tell which
            subsystem owns the transition.
        kind: Broad meaning of the node. This helps a caller distinguish
            between ordinary observations, recovery actions, waits, and hard
            terminal outcomes.
        summary: Short human-readable description of what the node means.
        evidence_keys: Names of runtime fields that should explain why the
            machine is currently in this node.
        evidence_description: Detailed explanation for how to interpret those
            runtime fields during audits or debugging.
        allowed_actions: Canonical action names that the corresponding handler
            may execute from this node. This is the machine-readable answer to
            "what can happen next from here?"
        action_description: Explanation of why those actions are legal in this
            node and what subsystem behavior they represent.
        exit_conditions: Named reasons that allow the machine to leave this
            node. These names are intentionally stable for tests and logs.
        exit_description: Detailed explanation of what concrete evidence or
            action result should satisfy each exit condition.
        terminal: Whether a caller should stop stepping this machine when this
            node is reached. Terminal only applies to this machine, not to the
            overall remote workflow in a higher layer.
    """

    id: Enum
    layer: str
    kind: StateKind
    summary: str
    evidence_keys: tuple[str, ...]
    evidence_description: str
    allowed_actions: tuple[str, ...]
    action_description: str
    exit_conditions: tuple[str, ...]
    exit_description: str
    terminal: bool


@dataclass(frozen=True)
class ConnectivitySnapshot:
    """Normalized evidence for host connectivity and recovery.

    Attributes:
        ssh_ok: Result of a direct SSH reachability probe. `True` means the
            caller can reach the host via SSH right now. `False` means the
            probe definitively failed. `None` means the adapter could not
            establish a reliable SSH answer and the state should be treated as
            ambiguous rather than conclusively down.
        bmc_ok: Result of the out-of-band management probe. `True` means the
            BMC channel is reachable and returned usable data. `False` means
            the BMC path definitively failed. `None` means there is no reliable
            BMC result for this probe cycle.
        host_powered_on: Best-effort machine power-state signal, usually from
            BMC. `True` means the host appears powered on. `False` means the
            host appears powered off or machine-level unavailable. `None` means
            the adapter could not determine host power state.
    """

    ssh_ok: bool | None
    bmc_ok: bool | None
    host_powered_on: bool | None


@dataclass(frozen=True)
class TmuxSnapshot:
    """Normalized evidence for tmux session and window management.

    Attributes:
        ssh_ok: Connectivity prerequisite for tmux control. `True` means the
            transport needed to talk to tmux is available. `False` means tmux
            cannot be managed because the host is not reachable yet. `None`
            means transport readiness could not be determined.
        session_exists: Whether the target tmux session already exists.
            `True` means session-level management can continue. `False` means
            the session must be created before any window management. `None`
            means the adapter could not determine session existence.
        session_healthy: Whether an existing session is usable for managed
            operations. `True` means the session is present and behaves as
            expected. `False` means the session exists but is degraded or not
            safe to rely on. `None` means health is unknown.
        window_exists: Whether the target task window already exists inside the
            managed session. `True` means task-level code can reuse the window.
            `False` means the session is ready but the window still needs to be
            created. `None` means the adapter could not determine window state.
    """

    ssh_ok: bool | None
    session_exists: bool | None
    session_healthy: bool | None
    window_exists: bool | None


@dataclass(frozen=True)
class TaskSnapshot:
    """Normalized evidence for one managed task window.

    Attributes:
        window_exists: Whether the target tmux window is present. `True` means
            the task can be inspected. `False` means task inspection cannot
            continue because the execution container is missing. `None` means
            the adapter could not determine window availability.
        command_active: Whether a command is currently running in the task
            window. `True` means the task is still in progress. `False` means
            there is no active command right now. `None` means activity could
            not be determined reliably.
        exit_code: Final exit code when available. `None` means no final code
            has been observed yet. `0` means success. Any non-zero value means
            the task ended in failure.
    """

    window_exists: bool | None
    command_active: bool | None
    exit_code: int | None


@dataclass
class ConnectivityContext:
    """Mutable runtime context for the connectivity machine.

    Attributes:
        auto_recover: Whether fault states may invoke recovery actions instead
            of immediately stopping in a failure node.
        last_probe: Most recent normalized connectivity evidence used to place
            the machine into a classified connectivity node.
        retry_count: Number of recovery-oriented retries already attempted by
            this machine instance. Callers can use it to reason about
            bounded recovery attempts.
        history: Ordered list of transition identifiers for later auditing.
        last_error: Best-effort explanation of the latest unrecoverable error.
    """

    auto_recover: bool
    last_probe: ConnectivitySnapshot | None = None
    retry_count: int = 0
    history: list[str] = field(default_factory=list)
    last_error: str | None = None


@dataclass
class TmuxContext:
    """Mutable runtime context for the tmux management machine.

    Attributes:
        auto_create: Whether the machine may create a missing session or window
            instead of stopping when required tmux objects are absent.
        last_probe: Most recent normalized tmux evidence used to classify the
            current node.
        ensure_session_attempted: Whether the machine already tried to create
            or recover the managed tmux session during this run.
        ensure_window_attempted: Whether the machine already tried to create
            the target task window during this run.
        history: Ordered list of transition identifiers for later auditing.
        last_error: Best-effort explanation for the latest tmux-level failure.
    """

    auto_create: bool
    last_probe: TmuxSnapshot | None = None
    ensure_session_attempted: bool = False
    ensure_window_attempted: bool = False
    history: list[str] = field(default_factory=list)
    last_error: str | None = None


@dataclass
class TaskContext:
    """Mutable runtime context for the task execution machine.

    Attributes:
        last_probe: Most recent task snapshot used to classify the current
            task node.
        history: Ordered list of transition identifiers for later auditing.
        last_error: Best-effort explanation for a task-level failure, such as
            a non-zero exit code or a missing task window.
    """

    last_probe: TaskSnapshot | None = None
    history: list[str] = field(default_factory=list)
    last_error: str | None = None


@dataclass(frozen=True)
class ConnectivityTransitionResult:
    """Structured result for one connectivity transition."""

    from_state: "ConnectivityStateId"
    to_state: "ConnectivityStateId"
    action: str
    reason: str
    context: ConnectivityContext


@dataclass(frozen=True)
class TmuxTransitionResult:
    """Structured result for one tmux transition."""

    from_state: "TmuxStateId"
    to_state: "TmuxStateId"
    action: str
    reason: str
    context: TmuxContext


@dataclass(frozen=True)
class TaskTransitionResult:
    """Structured result for one task transition."""

    from_state: "TaskStateId"
    to_state: "TaskStateId"
    action: str
    reason: str
    context: TaskContext


class ConnectivityControlAdapter(Protocol):
    """Required side-effect surface for the connectivity machine."""

    def probe_connectivity(self) -> ConnectivitySnapshot: ...
    def recover_host_power(self) -> ConnectivitySnapshot | None: ...
    def recover_ssh(self) -> ConnectivitySnapshot | None: ...


class TmuxControlAdapter(Protocol):
    """Required side-effect surface for the tmux machine."""

    def probe_tmux(self) -> TmuxSnapshot: ...
    def ensure_session(self) -> bool: ...
    def ensure_window(self) -> bool: ...


class TaskControlAdapter(Protocol):
    """Required side-effect surface for the task machine."""

    def inspect_task(self) -> TaskSnapshot: ...


class ConnectivityStateId(Enum):
    """Nodes for the connectivity and host-recovery machine."""

    UNKNOWN = "unknown"
    CHECKING_BMC = "checking_bmc"
    REMOTE_UNAVAILABLE = "remote_unavailable"
    HOST_POWERED_OFF = "host_powered_off"
    CHECKING_SSH = "checking_ssh"
    SSH_UNAVAILABLE = "ssh_unavailable"
    READY = "ready"
    FAILED = "failed"


class TmuxStateId(Enum):
    """Nodes for the tmux session/window management machine."""

    UNKNOWN = "unknown"
    PROBING = "probing"
    SESSION_MISSING = "session_missing"
    SESSION_READY = "session_ready"
    WINDOW_MISSING = "window_missing"
    WINDOW_READY = "window_ready"
    DEGRADED = "degraded"
    ENSURING_SESSION = "ensuring_session"
    ENSURING_WINDOW = "ensuring_window"
    FAILED = "failed"


class TaskStateId(Enum):
    """Nodes for the task execution machine."""

    UNKNOWN = "unknown"
    INSPECTING = "inspecting"
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OrchestrationStateId(Enum):
    """Top-level external state exposed by the remote toolkit.

    This machine is intentionally small. It does not re-describe the internal
    transitions of the connectivity/tmux/task machines. Instead, it tells an
    external caller which subsystem currently owns progress or blocks it.
    """

    UNKNOWN = "unknown"
    ENSURING_CONNECTIVITY = "ensuring_connectivity"
    BLOCKED_CONNECTIVITY = "blocked_connectivity"
    ENSURING_TMUX = "ensuring_tmux"
    BLOCKED_TMUX = "blocked_tmux"
    INSPECTING_TASK = "inspecting_task"
    TASK_IDLE = "task_idle"
    TASK_RUNNING = "task_running"
    TASK_SUCCEEDED = "task_succeeded"
    TASK_FAILED = "task_failed"


DEFAULT_CONNECTIVITY_STATE_SPECS: dict[ConnectivityStateId, StateSpec] = {
    ConnectivityStateId.UNKNOWN: StateSpec(
        id=ConnectivityStateId.UNKNOWN,
        layer="connectivity",
        kind=StateKind.OBSERVATION,
        summary="No connectivity evidence has been collected yet.",
        evidence_keys=(),
        evidence_description="This is the initial node before any BMC or SSH check begins.",
        allowed_actions=("probe_bmc",),
        action_description="The top-level connectivity flow always starts with BMC reachability.",
        exit_conditions=("bmc_check_started",),
        exit_description="Leave this node once the machine begins the BMC check.",
        terminal=False,
    ),
    ConnectivityStateId.CHECKING_BMC: StateSpec(
        id=ConnectivityStateId.CHECKING_BMC,
        layer="connectivity",
        kind=StateKind.ACTION,
        summary="The machine is checking whether BMC is reachable and whether the host is powered on.",
        evidence_keys=("last_probe",),
        evidence_description="A normalized connectivity snapshot is collected and the BMC portion is used first to decide whether remote control can continue.",
        allowed_actions=("probe_connectivity", "classify_bmc"),
        action_description="This node classifies BMC reachability and host power state before SSH is considered.",
        exit_conditions=("bmc_unreachable", "host_powered_off", "host_powered_on"),
        exit_description="Leave when BMC is unreachable, when the host is powered off, or when the host is powered on and SSH can be checked next.",
        terminal=False,
    ),
    ConnectivityStateId.REMOTE_UNAVAILABLE: StateSpec(
        id=ConnectivityStateId.REMOTE_UNAVAILABLE,
        layer="connectivity",
        kind=StateKind.TERMINAL,
        summary="BMC is unreachable, so the whole server is remotely unavailable.",
        evidence_keys=("last_probe", "last_error"),
        evidence_description="The last connectivity probe could not reach BMC, so the toolkit cannot make a trustworthy host-level decision.",
        allowed_actions=(),
        action_description="No automatic SSH or tmux action should proceed while BMC itself is unreachable.",
        exit_conditions=("new_connectivity_attempt_started",),
        exit_description="Leave only when a fresh connectivity attempt is started after BMC reachability is restored.",
        terminal=True,
    ),
    ConnectivityStateId.HOST_POWERED_OFF: StateSpec(
        id=ConnectivityStateId.HOST_POWERED_OFF,
        layer="connectivity",
        kind=StateKind.FAULT,
        summary="BMC is reachable, but the host appears powered off.",
        evidence_keys=("last_probe", "retry_count"),
        evidence_description="BMC responded successfully and indicated that the server is not powered on, so SSH cannot be expected yet.",
        allowed_actions=("recover_host_power",),
        action_description="If auto-recovery is enabled, the machine may perform one bounded power recovery action and then re-check BMC.",
        exit_conditions=("power_recovery_started", "recovery_disabled", "recovery_failed"),
        exit_description="Leave when power recovery starts, when auto-recovery is disabled, or when the recovery action fails.",
        terminal=False,
    ),
    ConnectivityStateId.CHECKING_SSH: StateSpec(
        id=ConnectivityStateId.CHECKING_SSH,
        layer="connectivity",
        kind=StateKind.ACTION,
        summary="BMC is healthy and the host is on, so the machine is checking SSH reachability.",
        evidence_keys=("last_probe",),
        evidence_description="The same connectivity snapshot is now interpreted from the SSH perspective because BMC and power state have already passed.",
        allowed_actions=("classify_ssh",),
        action_description="This node does not own recovery; it only decides whether SSH is ready or unavailable.",
        exit_conditions=("ssh_ready", "ssh_unavailable", "ssh_ambiguous"),
        exit_description="Leave when SSH is confirmed reachable, confirmed unavailable, or cannot be determined reliably.",
        terminal=False,
    ),
    ConnectivityStateId.SSH_UNAVAILABLE: StateSpec(
        id=ConnectivityStateId.SSH_UNAVAILABLE,
        layer="connectivity",
        kind=StateKind.FAULT,
        summary="BMC is healthy and the host is on, but SSH is unavailable.",
        evidence_keys=("last_probe", "retry_count"),
        evidence_description="BMC and host power are both good, but the SSH probe failed, so the blocker is narrowed to SSH-level availability.",
        allowed_actions=("recover_ssh",),
        action_description="If auto-recovery is enabled, the machine may perform one bounded SSH recovery action and then re-check BMC/SSH.",
        exit_conditions=("ssh_recovery_started", "recovery_disabled", "recovery_failed"),
        exit_description="Leave when SSH recovery starts, when auto-recovery is disabled, or when the recovery action fails.",
        terminal=False,
    ),
    ConnectivityStateId.READY: StateSpec(
        id=ConnectivityStateId.READY,
        layer="connectivity",
        kind=StateKind.TERMINAL,
        summary="BMC is reachable, the host is on, and SSH is reachable.",
        evidence_keys=("last_probe",),
        evidence_description="All required remote connectivity checks passed, so higher layers may continue into tmux management.",
        allowed_actions=(),
        action_description="Connectivity is complete; no further connectivity action is needed from this machine.",
        exit_conditions=("higher_layer_consumed_ready",),
        exit_description="Leave only when a higher layer starts tmux management or a new connectivity run.",
        terminal=True,
    ),
    ConnectivityStateId.FAILED: StateSpec(
        id=ConnectivityStateId.FAILED,
        layer="connectivity",
        kind=StateKind.TERMINAL,
        summary="Connectivity recovery could not produce a stable answer.",
        evidence_keys=("last_error", "retry_count"),
        evidence_description="A bounded recovery action failed or returned unusable evidence, so the caller must intervene.",
        allowed_actions=(),
        action_description="A higher layer or human operator must decide the next step.",
        exit_conditions=("new_machine_instance_started",),
        exit_description="Leave only by constructing a fresh connectivity machine.",
        terminal=True,
    ),
}


DEFAULT_TMUX_STATE_SPECS: dict[TmuxStateId, StateSpec] = {
    TmuxStateId.UNKNOWN: StateSpec(
        id=TmuxStateId.UNKNOWN,
        layer="tmux",
        kind=StateKind.OBSERVATION,
        summary="No tmux session/window evidence has been collected yet.",
        evidence_keys=(),
        evidence_description="This is the initial node before the first tmux probe.",
        allowed_actions=("probe_tmux",),
        action_description="The machine must first inspect tmux session and window state.",
        exit_conditions=("first_probe_started",),
        exit_description="Leave once tmux probing begins.",
        terminal=False,
    ),
    TmuxStateId.PROBING: StateSpec(
        id=TmuxStateId.PROBING,
        layer="tmux",
        kind=StateKind.ACTION,
        summary="The machine is classifying tmux session/window evidence.",
        evidence_keys=("last_probe",),
        evidence_description="A normalized tmux snapshot is collected and mapped to a stable tmux node.",
        allowed_actions=("probe_tmux", "classify_tmux"),
        action_description="This node turns raw tmux evidence into a session/window classification.",
        exit_conditions=("classified_ready", "classified_fault"),
        exit_description="Leave once the last tmux snapshot has been classified.",
        terminal=False,
    ),
    TmuxStateId.SESSION_MISSING: StateSpec(
        id=TmuxStateId.SESSION_MISSING,
        layer="tmux",
        kind=StateKind.FAULT,
        summary="The managed tmux session does not exist yet.",
        evidence_keys=("last_probe", "ensure_session_attempted"),
        evidence_description="Connectivity is available, but there is no session to host managed windows.",
        allowed_actions=("ensure_session",),
        action_description="This node may create or recover the session when auto-create is enabled.",
        exit_conditions=("session_creation_started", "creation_disabled"),
        exit_description="Leave when session creation starts or when auto-create is disabled.",
        terminal=False,
    ),
    TmuxStateId.SESSION_READY: StateSpec(
        id=TmuxStateId.SESSION_READY,
        layer="tmux",
        kind=StateKind.OBSERVATION,
        summary="The managed tmux session exists and is healthy.",
        evidence_keys=("last_probe",),
        evidence_description="Session-level readiness is satisfied, so the next concern is the task window.",
        allowed_actions=(),
        action_description="This node is consumed by higher tmux window handling rather than running side effects itself.",
        exit_conditions=("window_status_consumed",),
        exit_description="Leave when the caller continues to window-level handling.",
        terminal=False,
    ),
    TmuxStateId.WINDOW_MISSING: StateSpec(
        id=TmuxStateId.WINDOW_MISSING,
        layer="tmux",
        kind=StateKind.FAULT,
        summary="The managed tmux session exists, but the target window is missing.",
        evidence_keys=("last_probe", "ensure_window_attempted"),
        evidence_description="Session management succeeded, but the specific window needed for task control does not exist yet.",
        allowed_actions=("ensure_window",),
        action_description="This node may create the target window when auto-create is enabled.",
        exit_conditions=("window_creation_started", "creation_disabled"),
        exit_description="Leave when window creation starts or when auto-create is disabled.",
        terminal=False,
    ),
    TmuxStateId.WINDOW_READY: StateSpec(
        id=TmuxStateId.WINDOW_READY,
        layer="tmux",
        kind=StateKind.TERMINAL,
        summary="The managed tmux session and target window are ready for task inspection.",
        evidence_keys=("last_probe",),
        evidence_description="Both session and window readiness are satisfied, so task-level logic may begin.",
        allowed_actions=(),
        action_description="No further tmux action is required before handing off to the task machine.",
        exit_conditions=("higher_layer_consumed_ready",),
        exit_description="Leave only when a higher layer or another machine continues into task handling.",
        terminal=True,
    ),
    TmuxStateId.DEGRADED: StateSpec(
        id=TmuxStateId.DEGRADED,
        layer="tmux",
        kind=StateKind.FAULT,
        summary="Tmux signals are partial, contradictory, or blocked by missing SSH.",
        evidence_keys=("last_probe", "last_error"),
        evidence_description="The tmux probe did not produce a clean session/window classification, or tmux could not be controlled because connectivity was not ready.",
        allowed_actions=(),
        action_description="This node does not recover connectivity by itself; the caller must first satisfy connectivity prerequisites or inspect tmux health manually.",
        exit_conditions=("new_probe_started", "caller_aborts"),
        exit_description="Leave when a fresh tmux probe starts under better prerequisites or when the caller stops.",
        terminal=True,
    ),
    TmuxStateId.ENSURING_SESSION: StateSpec(
        id=TmuxStateId.ENSURING_SESSION,
        layer="tmux",
        kind=StateKind.ACTION,
        summary="The machine is creating or recovering the managed tmux session.",
        evidence_keys=("ensure_session_attempted",),
        evidence_description="A concrete session-level creation or recovery action has just been issued.",
        allowed_actions=("ensure_session", "probe_tmux"),
        action_description="After session creation, the next step is to re-enter controlled tmux probing.",
        exit_conditions=("session_created", "session_creation_failed"),
        exit_description="Leave when the session action either succeeds and returns to probing or fails terminally.",
        terminal=False,
    ),
    TmuxStateId.ENSURING_WINDOW: StateSpec(
        id=TmuxStateId.ENSURING_WINDOW,
        layer="tmux",
        kind=StateKind.ACTION,
        summary="The machine is creating the target tmux window.",
        evidence_keys=("ensure_window_attempted",),
        evidence_description="A concrete window creation action has just been issued inside a healthy session.",
        allowed_actions=("ensure_window", "probe_tmux"),
        action_description="After window creation, the next step is to re-enter controlled tmux probing.",
        exit_conditions=("window_created", "window_creation_failed"),
        exit_description="Leave when the window action either succeeds and returns to probing or fails terminally.",
        terminal=False,
    ),
    TmuxStateId.FAILED: StateSpec(
        id=TmuxStateId.FAILED,
        layer="tmux",
        kind=StateKind.TERMINAL,
        summary="Tmux management stopped without producing a usable session/window.",
        evidence_keys=("last_error",),
        evidence_description="No further automatic tmux action remains for this machine instance.",
        allowed_actions=(),
        action_description="A higher layer or human operator must decide the next step.",
        exit_conditions=("new_machine_instance_started",),
        exit_description="Leave only by constructing a fresh tmux machine.",
        terminal=True,
    ),
}


DEFAULT_TASK_STATE_SPECS: dict[TaskStateId, StateSpec] = {
    TaskStateId.UNKNOWN: StateSpec(
        id=TaskStateId.UNKNOWN,
        layer="task",
        kind=StateKind.OBSERVATION,
        summary="No task-window evidence has been inspected yet.",
        evidence_keys=(),
        evidence_description="This is the initial node before the first task inspection.",
        allowed_actions=("inspect_task",),
        action_description="The only legal next step is to inspect the task window.",
        exit_conditions=("first_inspection_started",),
        exit_description="Leave once task inspection begins.",
        terminal=False,
    ),
    TaskStateId.INSPECTING: StateSpec(
        id=TaskStateId.INSPECTING,
        layer="task",
        kind=StateKind.ACTION,
        summary="The machine is classifying task activity inside the window.",
        evidence_keys=("last_probe",),
        evidence_description="A normalized task snapshot is collected and mapped to a stable task node.",
        allowed_actions=("inspect_task", "classify_task"),
        action_description="This node turns raw task evidence into a final or ongoing task state.",
        exit_conditions=("classified_idle", "classified_running", "classified_terminal"),
        exit_description="Leave once the task snapshot has been classified.",
        terminal=False,
    ),
    TaskStateId.IDLE: StateSpec(
        id=TaskStateId.IDLE,
        layer="task",
        kind=StateKind.TERMINAL,
        summary="The task window exists, but no command is currently running.",
        evidence_keys=("last_probe",),
        evidence_description="Task infrastructure is present, yet no active command or final exit code is currently observed.",
        allowed_actions=(),
        action_description="A higher layer decides whether to send a new command.",
        exit_conditions=("new_task_started", "new_inspection_started"),
        exit_description="Leave when a command is sent or a new inspection cycle begins.",
        terminal=True,
    ),
    TaskStateId.RUNNING: StateSpec(
        id=TaskStateId.RUNNING,
        layer="task",
        kind=StateKind.OBSERVATION,
        summary="A command is currently active in the managed task window.",
        evidence_keys=("last_probe",),
        evidence_description="Task inspection reports an active command and no final exit code yet.",
        allowed_actions=("inspect_task",),
        action_description="The machine simply polls for later task completion or failure.",
        exit_conditions=("task_completed", "task_failed"),
        exit_description="Leave once inspection observes a final exit code or another terminal task condition.",
        terminal=False,
    ),
    TaskStateId.SUCCEEDED: StateSpec(
        id=TaskStateId.SUCCEEDED,
        layer="task",
        kind=StateKind.TERMINAL,
        summary="The managed task completed successfully.",
        evidence_keys=("last_probe",),
        evidence_description="Task inspection found a final zero exit code.",
        allowed_actions=(),
        action_description="The caller may now consume the result or start a new task.",
        exit_conditions=("new_task_started", "new_machine_instance_started"),
        exit_description="Leave only when the caller initiates another task lifecycle.",
        terminal=True,
    ),
    TaskStateId.FAILED: StateSpec(
        id=TaskStateId.FAILED,
        layer="task",
        kind=StateKind.TERMINAL,
        summary="The managed task failed or could not be inspected safely.",
        evidence_keys=("last_probe", "last_error"),
        evidence_description="Task inspection found a non-zero exit code, a missing window, or another terminal task fault.",
        allowed_actions=(),
        action_description="The caller must inspect logs or repair the task container before continuing.",
        exit_conditions=("new_task_started", "new_machine_instance_started"),
        exit_description="Leave only when the caller initiates a fresh task lifecycle.",
        terminal=True,
    ),
}


DEFAULT_ORCHESTRATION_STATE_SPECS: dict[OrchestrationStateId, StateSpec] = {
    OrchestrationStateId.UNKNOWN: StateSpec(
        id=OrchestrationStateId.UNKNOWN,
        layer="orchestration",
        kind=StateKind.OBSERVATION,
        summary="No subsystem has been stepped yet.",
        evidence_keys=(),
        evidence_description="This is the initial orchestration node before connectivity ownership begins.",
        allowed_actions=("step_connectivity",),
        action_description="The top-level machine always begins by delegating control to the connectivity machine.",
        exit_conditions=("connectivity_phase_started",),
        exit_description="Leave once the orchestration machine enters connectivity ownership.",
        terminal=False,
    ),
    OrchestrationStateId.ENSURING_CONNECTIVITY: StateSpec(
        id=OrchestrationStateId.ENSURING_CONNECTIVITY,
        layer="orchestration",
        kind=StateKind.ACTION,
        summary="Connectivity owns progress and must reach READY first.",
        evidence_keys=("connectivity_state",),
        evidence_description="The top-level machine is waiting for the connectivity machine to converge to READY or FAIL.",
        allowed_actions=("step_connectivity",),
        action_description="Only connectivity is allowed to move the workflow from this node.",
        exit_conditions=("connectivity_ready", "connectivity_blocked"),
        exit_description="Leave when connectivity reaches READY or a terminal non-ready state.",
        terminal=False,
    ),
    OrchestrationStateId.BLOCKED_CONNECTIVITY: StateSpec(
        id=OrchestrationStateId.BLOCKED_CONNECTIVITY,
        layer="orchestration",
        kind=StateKind.TERMINAL,
        summary="The workflow is blocked before tmux because connectivity did not become ready.",
        evidence_keys=("connectivity_state", "last_error"),
        evidence_description="The connectivity machine reached a terminal non-ready node such as FAILED.",
        allowed_actions=(),
        action_description="A caller must inspect connectivity details or start a fresh attempt.",
        exit_conditions=("new_orchestration_started",),
        exit_description="Leave only when a new orchestration instance is created.",
        terminal=True,
    ),
    OrchestrationStateId.ENSURING_TMUX: StateSpec(
        id=OrchestrationStateId.ENSURING_TMUX,
        layer="orchestration",
        kind=StateKind.ACTION,
        summary="Tmux owns progress once connectivity is ready.",
        evidence_keys=("tmux_state",),
        evidence_description="The top-level machine is waiting for the tmux machine to produce a reusable task window.",
        allowed_actions=("step_tmux",),
        action_description="Only tmux session/window management is allowed to move the workflow from this node.",
        exit_conditions=("tmux_ready", "tmux_blocked"),
        exit_description="Leave when tmux reaches WINDOW_READY or a terminal non-ready state.",
        terminal=False,
    ),
    OrchestrationStateId.BLOCKED_TMUX: StateSpec(
        id=OrchestrationStateId.BLOCKED_TMUX,
        layer="orchestration",
        kind=StateKind.TERMINAL,
        summary="Connectivity is ready, but tmux could not produce a usable task window.",
        evidence_keys=("tmux_state", "last_error"),
        evidence_description="The tmux machine reached a terminal non-ready node such as DEGRADED or FAILED.",
        allowed_actions=(),
        action_description="A caller must inspect tmux details or start a fresh attempt.",
        exit_conditions=("new_orchestration_started",),
        exit_description="Leave only when a new orchestration instance is created.",
        terminal=True,
    ),
    OrchestrationStateId.INSPECTING_TASK: StateSpec(
        id=OrchestrationStateId.INSPECTING_TASK,
        layer="orchestration",
        kind=StateKind.ACTION,
        summary="Task inspection owns progress once a tmux window is ready.",
        evidence_keys=("task_state",),
        evidence_description="The top-level machine is waiting for the task machine to report idle, running, or final outcome.",
        allowed_actions=("step_task",),
        action_description="Only task inspection is allowed to move the workflow from this node.",
        exit_conditions=("task_idle", "task_running", "task_succeeded", "task_failed"),
        exit_description="Leave when task inspection reaches a stable task-level state.",
        terminal=False,
    ),
    OrchestrationStateId.TASK_IDLE: StateSpec(
        id=OrchestrationStateId.TASK_IDLE,
        layer="orchestration",
        kind=StateKind.TERMINAL,
        summary="Infrastructure is ready and the task window is idle.",
        evidence_keys=("task_state",),
        evidence_description="The task machine reported that no command is running in the prepared window.",
        allowed_actions=(),
        action_description="The caller may now send a command or stop.",
        exit_conditions=("new_orchestration_started", "new_task_started"),
        exit_description="Leave only when a fresh orchestration or task lifecycle begins.",
        terminal=True,
    ),
    OrchestrationStateId.TASK_RUNNING: StateSpec(
        id=OrchestrationStateId.TASK_RUNNING,
        layer="orchestration",
        kind=StateKind.OBSERVATION,
        summary="Infrastructure is ready and the task is currently running.",
        evidence_keys=("task_state",),
        evidence_description="The task machine reported an active command and remains responsible for future progress.",
        allowed_actions=("step_task",),
        action_description="The top-level machine simply continues polling the task machine.",
        exit_conditions=("task_succeeded", "task_failed"),
        exit_description="Leave when task polling reaches a terminal result.",
        terminal=False,
    ),
    OrchestrationStateId.TASK_SUCCEEDED: StateSpec(
        id=OrchestrationStateId.TASK_SUCCEEDED,
        layer="orchestration",
        kind=StateKind.TERMINAL,
        summary="The task completed successfully through the prepared remote stack.",
        evidence_keys=("task_state",),
        evidence_description="The task machine reported a final success outcome.",
        allowed_actions=(),
        action_description="The caller may now consume results or start a fresh task lifecycle.",
        exit_conditions=("new_orchestration_started", "new_task_started"),
        exit_description="Leave only when a fresh orchestration or task lifecycle begins.",
        terminal=True,
    ),
    OrchestrationStateId.TASK_FAILED: StateSpec(
        id=OrchestrationStateId.TASK_FAILED,
        layer="orchestration",
        kind=StateKind.TERMINAL,
        summary="The task failed after infrastructure preparation succeeded.",
        evidence_keys=("task_state", "last_error"),
        evidence_description="The task machine reported a terminal failure outcome.",
        allowed_actions=(),
        action_description="The caller must inspect task logs or restart the workflow.",
        exit_conditions=("new_orchestration_started", "new_task_started"),
        exit_description="Leave only when a fresh orchestration or task lifecycle begins.",
        terminal=True,
    ),
}


@dataclass
class OrchestrationContext:
    """Mutable runtime context for the top-level external orchestration machine.

    Attributes:
        connectivity_state: Latest public state of the connectivity machine.
        tmux_state: Latest public state of the tmux machine.
        task_state: Latest public state of the task machine.
        history: Ordered list of top-level transitions for later auditing.
        last_error: Best-effort summary of the blocking subsystem's failure.
    """

    connectivity_state: ConnectivityStateId | None = None
    tmux_state: TmuxStateId | None = None
    task_state: TaskStateId | None = None
    history: list[str] = field(default_factory=list)
    last_error: str | None = None


@dataclass(frozen=True)
class OrchestrationTransitionResult:
    """Structured result for one top-level orchestration transition."""

    from_state: OrchestrationStateId
    to_state: OrchestrationStateId
    action: str
    reason: str
    context: OrchestrationContext


class ConnectivityStateMachine:
    """Explicit machine for BMC/SSH readiness and host recovery."""

    def __init__(self, adapter: ConnectivityControlAdapter, auto_recover: bool):
        self.adapter = adapter
        self.context = ConnectivityContext(auto_recover=auto_recover)
        self.current_state = ConnectivityStateId.UNKNOWN

    def get_state_spec(self, state_id: ConnectivityStateId | None = None) -> StateSpec:
        """Return the static attribute spec for one connectivity node."""
        return DEFAULT_CONNECTIVITY_STATE_SPECS[state_id or self.current_state]

    def is_terminal(self) -> bool:
        """Whether the current connectivity node is terminal."""
        return self.get_state_spec().terminal

    def step(self) -> ConnectivityTransitionResult:
        """Advance connectivity by one controlled transition."""
        state = self.current_state

        if state == ConnectivityStateId.UNKNOWN:
            return self._transition(ConnectivityStateId.CHECKING_BMC, "probe_bmc", "Starting connectivity flow with a BMC check.")
        if state == ConnectivityStateId.CHECKING_BMC:
            return self._handle_checking_bmc()
        if state == ConnectivityStateId.CHECKING_SSH:
            return self._handle_checking_ssh()
        if state == ConnectivityStateId.REMOTE_UNAVAILABLE:
            return self._transition(ConnectivityStateId.REMOTE_UNAVAILABLE, "stop", "BMC is unreachable, so remote connectivity is unavailable.")
        if state == ConnectivityStateId.HOST_POWERED_OFF:
            return self._handle_host_powered_off()
        if state == ConnectivityStateId.SSH_UNAVAILABLE:
            return self._handle_ssh_unavailable()
        if state == ConnectivityStateId.READY:
            return self._transition(ConnectivityStateId.READY, "hold_ready", "Connectivity is ready for tmux management.")
        if state == ConnectivityStateId.FAILED:
            return self._transition(ConnectivityStateId.FAILED, "stop", "Connectivity machine is terminal.")

        raise RuntimeError(f"Unhandled connectivity state: {state}")

    def _handle_checking_bmc(self) -> ConnectivityTransitionResult:
        snapshot = self.adapter.probe_connectivity()
        self.context.last_probe = snapshot
        if snapshot.bmc_ok is False:
            self.context.last_error = "BMC is unreachable."
            return self._transition(ConnectivityStateId.REMOTE_UNAVAILABLE, "classify_bmc", "BMC is unreachable, so the server is remotely unavailable.")
        if snapshot.bmc_ok is None and snapshot.host_powered_on is None and snapshot.ssh_ok is None:
            return self._fail("BMC is not configured.")
        if snapshot.host_powered_on is False:
            return self._transition(ConnectivityStateId.HOST_POWERED_OFF, "classify_bmc", "BMC is reachable and reports the host as powered off.")
        if snapshot.host_powered_on is True:
            return self._transition(ConnectivityStateId.CHECKING_SSH, "classify_bmc", "BMC is reachable and reports the host as powered on.")
        return self._fail("BMC is reachable but host power state is unknown.")

    def _handle_checking_ssh(self) -> ConnectivityTransitionResult:
        snapshot = self.context.last_probe
        if snapshot is None:
            return self._fail("SSH check requested without a prior BMC probe.")
        if snapshot.ssh_ok is True:
            return self._transition(ConnectivityStateId.READY, "classify_ssh", "SSH is reachable.")
        if snapshot.ssh_ok is False:
            return self._transition(ConnectivityStateId.SSH_UNAVAILABLE, "classify_ssh", "SSH is unavailable while BMC reports the host is powered on.")
        return self._fail("SSH reachability is unknown.")

    def _handle_host_powered_off(self) -> ConnectivityTransitionResult:
        if not self.context.auto_recover:
            return self._fail("Automatic recovery disabled while host is powered off.")

        self.context.retry_count += 1
        snapshot = self.adapter.recover_host_power()
        if snapshot is None:
            return self._fail("Power recovery action failed.")
        self.context.last_probe = snapshot
        return self._transition(ConnectivityStateId.CHECKING_BMC, "recover_host_power", "Power recovery action completed; re-checking BMC state.")

    def _handle_ssh_unavailable(self) -> ConnectivityTransitionResult:
        if not self.context.auto_recover:
            return self._fail("Automatic recovery disabled while SSH is unavailable.")

        self.context.retry_count += 1
        snapshot = self.adapter.recover_ssh()
        if snapshot is None:
            return self._fail("SSH recovery action failed.")
        self.context.last_probe = snapshot
        return self._transition(ConnectivityStateId.CHECKING_BMC, "recover_ssh", "SSH recovery action completed; re-checking BMC and SSH.")

    def _transition(
        self,
        to_state: ConnectivityStateId,
        action: str,
        reason: str,
    ) -> ConnectivityTransitionResult:
        previous = self.current_state
        self.current_state = to_state
        self.context.history.append(f"{previous.value}->{to_state.value}:{action}")
        return ConnectivityTransitionResult(previous, to_state, action, reason, self.context)

    def _fail(self, reason: str) -> ConnectivityTransitionResult:
        self.context.last_error = reason
        return self._transition(ConnectivityStateId.FAILED, "fail", reason)


class TmuxStateMachine:
    """Explicit machine for tmux session and window availability."""

    def __init__(self, adapter: TmuxControlAdapter, auto_create: bool):
        self.adapter = adapter
        self.context = TmuxContext(auto_create=auto_create)
        self.current_state = TmuxStateId.UNKNOWN

    def get_state_spec(self, state_id: TmuxStateId | None = None) -> StateSpec:
        """Return the static attribute spec for one tmux node."""
        return DEFAULT_TMUX_STATE_SPECS[state_id or self.current_state]

    def is_terminal(self) -> bool:
        """Whether the current tmux node is terminal."""
        return self.get_state_spec().terminal

    def step(self) -> TmuxTransitionResult:
        """Advance tmux management by one controlled transition."""
        state = self.current_state

        if state == TmuxStateId.UNKNOWN:
            return self._transition(TmuxStateId.PROBING, "probe_tmux", "Starting first tmux probe.")
        if state == TmuxStateId.PROBING:
            return self._handle_probing()
        if state == TmuxStateId.SESSION_MISSING:
            return self._handle_session_missing()
        if state == TmuxStateId.SESSION_READY:
            return self._handle_session_ready()
        if state == TmuxStateId.WINDOW_MISSING:
            return self._handle_window_missing()
        if state == TmuxStateId.WINDOW_READY:
            return self._transition(TmuxStateId.WINDOW_READY, "hold_ready", "Tmux session and window are ready for task handling.")
        if state == TmuxStateId.DEGRADED:
            return self._transition(TmuxStateId.DEGRADED, "stop", "Tmux machine is blocked by degraded or missing prerequisites.")
        if state == TmuxStateId.ENSURING_SESSION:
            return self._handle_ensuring_session()
        if state == TmuxStateId.ENSURING_WINDOW:
            return self._handle_ensuring_window()
        if state == TmuxStateId.FAILED:
            return self._transition(TmuxStateId.FAILED, "stop", "Tmux machine is terminal.")

        raise RuntimeError(f"Unhandled tmux state: {state}")

    def _handle_probing(self) -> TmuxTransitionResult:
        snapshot = self.adapter.probe_tmux()
        self.context.last_probe = snapshot
        classified = self._classify_probe(snapshot)
        return self._transition(classified, "classify_tmux", f"Tmux probe classified as {classified.value}.")

    def _handle_session_missing(self) -> TmuxTransitionResult:
        if not self.context.auto_create:
            return self._fail("Automatic tmux session creation is disabled.")

        self.context.ensure_session_attempted = True
        if self.adapter.ensure_session():
            return self._transition(TmuxStateId.ENSURING_SESSION, "ensure_session", "Starting tmux session creation.")
        return self._fail("Tmux session creation failed to start.")

    def _handle_session_ready(self) -> TmuxTransitionResult:
        snapshot = self.context.last_probe
        if snapshot and snapshot.window_exists is False:
            return self._transition(TmuxStateId.WINDOW_MISSING, "use_cached_probe", "Session is ready but target window is missing.")
        if snapshot and snapshot.window_exists is True:
            return self._transition(TmuxStateId.WINDOW_READY, "use_cached_probe", "Session and window are ready.")
        return self._fail("Session is ready but window state is unknown.")

    def _handle_window_missing(self) -> TmuxTransitionResult:
        if not self.context.auto_create:
            return self._fail("Automatic tmux window creation is disabled.")

        self.context.ensure_window_attempted = True
        if self.adapter.ensure_window():
            return self._transition(TmuxStateId.ENSURING_WINDOW, "ensure_window", "Starting tmux window creation.")
        return self._fail("Tmux window creation failed to start.")

    def _handle_ensuring_session(self) -> TmuxTransitionResult:
        return self._transition(TmuxStateId.PROBING, "probe_tmux", "Session action issued; returning to controlled tmux probe.")

    def _handle_ensuring_window(self) -> TmuxTransitionResult:
        return self._transition(TmuxStateId.PROBING, "probe_tmux", "Window action issued; returning to controlled tmux probe.")

    def _classify_probe(self, snapshot: TmuxSnapshot) -> TmuxStateId:
        """Map tmux evidence to one tmux node."""
        if snapshot.ssh_ok is not True:
            self.context.last_error = "Tmux control is blocked because connectivity is not ready."
            return TmuxStateId.DEGRADED
        if snapshot.session_exists is False:
            return TmuxStateId.SESSION_MISSING
        if snapshot.session_exists is True and snapshot.session_healthy is not True:
            self.context.last_error = "Tmux session exists but is not healthy."
            return TmuxStateId.DEGRADED
        if snapshot.session_exists is True and snapshot.session_healthy is True and snapshot.window_exists is False:
            return TmuxStateId.SESSION_READY
        if snapshot.session_exists is True and snapshot.session_healthy is True and snapshot.window_exists is True:
            return TmuxStateId.WINDOW_READY
        return TmuxStateId.DEGRADED

    def _transition(
        self,
        to_state: TmuxStateId,
        action: str,
        reason: str,
    ) -> TmuxTransitionResult:
        previous = self.current_state
        self.current_state = to_state
        self.context.history.append(f"{previous.value}->{to_state.value}:{action}")
        return TmuxTransitionResult(previous, to_state, action, reason, self.context)

    def _fail(self, reason: str) -> TmuxTransitionResult:
        self.context.last_error = reason
        return self._transition(TmuxStateId.FAILED, "fail", reason)


class TaskStateMachine:
    """Explicit machine for one managed task window."""

    def __init__(self, adapter: TaskControlAdapter):
        self.adapter = adapter
        self.context = TaskContext()
        self.current_state = TaskStateId.UNKNOWN

    def get_state_spec(self, state_id: TaskStateId | None = None) -> StateSpec:
        """Return the static attribute spec for one task node."""
        return DEFAULT_TASK_STATE_SPECS[state_id or self.current_state]

    def is_terminal(self) -> bool:
        """Whether the current task node is terminal."""
        return self.get_state_spec().terminal

    def step(self) -> TaskTransitionResult:
        """Advance task inspection by one controlled transition."""
        state = self.current_state

        if state == TaskStateId.UNKNOWN:
            return self._transition(TaskStateId.INSPECTING, "inspect_task", "Starting first task inspection.")
        if state == TaskStateId.INSPECTING:
            return self._handle_inspecting()
        if state == TaskStateId.RUNNING:
            return self._handle_running()
        if state == TaskStateId.IDLE:
            return self._transition(TaskStateId.IDLE, "hold_idle", "Task window is idle and waiting for a new command.")
        if state == TaskStateId.SUCCEEDED:
            return self._transition(TaskStateId.SUCCEEDED, "hold_succeeded", "Task already completed successfully.")
        if state == TaskStateId.FAILED:
            return self._transition(TaskStateId.FAILED, "hold_failed", "Task machine is terminal after failure.")

        raise RuntimeError(f"Unhandled task state: {state}")

    def _handle_inspecting(self) -> TaskTransitionResult:
        snapshot = self.adapter.inspect_task()
        self.context.last_probe = snapshot
        classified = self._classify_probe(snapshot)
        reason_map = {
            TaskStateId.IDLE: "Task window exists but no command is running.",
            TaskStateId.RUNNING: "Task command is currently active.",
            TaskStateId.SUCCEEDED: "Task completed successfully.",
            TaskStateId.FAILED: self.context.last_error or "Task inspection failed.",
        }
        return self._transition(classified, "classify_task", reason_map[classified])

    def _handle_running(self) -> TaskTransitionResult:
        return self._transition(TaskStateId.INSPECTING, "inspect_task", "Polling running task for a new snapshot.")

    def _classify_probe(self, snapshot: TaskSnapshot) -> TaskStateId:
        """Map task-window evidence to one task node."""
        if snapshot.window_exists is not True:
            self.context.last_error = "Managed task window is missing."
            return TaskStateId.FAILED
        if snapshot.command_active is True:
            return TaskStateId.RUNNING
        if snapshot.exit_code == 0:
            return TaskStateId.SUCCEEDED
        if snapshot.exit_code is not None and snapshot.exit_code != 0:
            self.context.last_error = f"Task failed with exit code {snapshot.exit_code}."
            return TaskStateId.FAILED
        if snapshot.command_active is False and snapshot.exit_code is None:
            return TaskStateId.IDLE
        self.context.last_error = "Task state is ambiguous."
        return TaskStateId.FAILED

    def _transition(
        self,
        to_state: TaskStateId,
        action: str,
        reason: str,
    ) -> TaskTransitionResult:
        previous = self.current_state
        self.current_state = to_state
        self.context.history.append(f"{previous.value}->{to_state.value}:{action}")
        return TaskTransitionResult(previous, to_state, action, reason, self.context)


class RemoteOrchestrationMachine:
    """Thin top-level machine that exposes subsystem interaction to callers.

    This class is the public orchestration surface for the external toolkit.
    It does not duplicate the internals of the three subsystem machines. Its
    job is only to sequence ownership:

    connectivity -> tmux -> task
    """

    def __init__(
        self,
        connectivity: ConnectivityStateMachine,
        tmux: TmuxStateMachine,
        task: TaskStateMachine,
    ):
        self.connectivity = connectivity
        self.tmux = tmux
        self.task = task
        self.context = OrchestrationContext()
        self.current_state = OrchestrationStateId.UNKNOWN

    def get_state_spec(self, state_id: OrchestrationStateId | None = None) -> StateSpec:
        """Return the static attribute spec for one orchestration node."""
        return DEFAULT_ORCHESTRATION_STATE_SPECS[state_id or self.current_state]

    def is_terminal(self) -> bool:
        """Whether the current orchestration node is terminal."""
        return self.get_state_spec().terminal

    def step(self) -> OrchestrationTransitionResult:
        """Advance the top-level orchestration by delegating to one subsystem."""
        state = self.current_state

        if state == OrchestrationStateId.UNKNOWN:
            return self._transition(
                OrchestrationStateId.ENSURING_CONNECTIVITY,
                "step_connectivity",
                "Starting top-level orchestration with connectivity ownership.",
            )
        if state == OrchestrationStateId.ENSURING_CONNECTIVITY:
            return self._handle_connectivity()
        if state == OrchestrationStateId.ENSURING_TMUX:
            return self._handle_tmux()
        if state == OrchestrationStateId.INSPECTING_TASK:
            return self._handle_task()
        if state == OrchestrationStateId.TASK_RUNNING:
            return self._handle_task()
        if self.is_terminal():
            return self._transition(self.current_state, "stop", "Top-level orchestration is terminal.")

        raise RuntimeError(f"Unhandled orchestration state: {state}")

    def _handle_connectivity(self) -> OrchestrationTransitionResult:
        transition = self.connectivity.step()
        self.context.connectivity_state = transition.to_state
        if transition.to_state == ConnectivityStateId.READY:
            return self._transition(
                OrchestrationStateId.ENSURING_TMUX,
                "step_tmux",
                "Connectivity is ready; handing ownership to tmux management.",
            )
        if self.connectivity.is_terminal():
            self.context.last_error = transition.reason
            return self._transition(
                OrchestrationStateId.BLOCKED_CONNECTIVITY,
                "block_connectivity",
                f"Connectivity blocked orchestration: {transition.reason}",
            )
        return self._transition(
            OrchestrationStateId.ENSURING_CONNECTIVITY,
            "step_connectivity",
            f"Connectivity remains in {transition.to_state.value}.",
        )

    def _handle_tmux(self) -> OrchestrationTransitionResult:
        transition = self.tmux.step()
        self.context.tmux_state = transition.to_state
        if transition.to_state == TmuxStateId.WINDOW_READY:
            return self._transition(
                OrchestrationStateId.INSPECTING_TASK,
                "step_task",
                "Tmux window is ready; handing ownership to task inspection.",
            )
        if self.tmux.is_terminal():
            self.context.last_error = transition.reason
            return self._transition(
                OrchestrationStateId.BLOCKED_TMUX,
                "block_tmux",
                f"Tmux blocked orchestration: {transition.reason}",
            )
        return self._transition(
            OrchestrationStateId.ENSURING_TMUX,
            "step_tmux",
            f"Tmux remains in {transition.to_state.value}.",
        )

    def _handle_task(self) -> OrchestrationTransitionResult:
        transition = self.task.step()
        self.context.task_state = transition.to_state
        state_map = {
            TaskStateId.IDLE: OrchestrationStateId.TASK_IDLE,
            TaskStateId.RUNNING: OrchestrationStateId.TASK_RUNNING,
            TaskStateId.SUCCEEDED: OrchestrationStateId.TASK_SUCCEEDED,
            TaskStateId.FAILED: OrchestrationStateId.TASK_FAILED,
        }
        if transition.to_state in state_map:
            if transition.to_state == TaskStateId.FAILED:
                self.context.last_error = transition.reason
            return self._transition(
                state_map[transition.to_state],
                "step_task",
                f"Task machine reported {transition.to_state.value}.",
            )
        return self._transition(
            OrchestrationStateId.INSPECTING_TASK,
            "step_task",
            f"Task remains in {transition.to_state.value}.",
        )

    def _transition(
        self,
        to_state: OrchestrationStateId,
        action: str,
        reason: str,
    ) -> OrchestrationTransitionResult:
        previous = self.current_state
        self.current_state = to_state
        self.context.history.append(f"{previous.value}->{to_state.value}:{action}")
        return OrchestrationTransitionResult(previous, to_state, action, reason, self.context)
