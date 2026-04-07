"""Remote tmux session manager."""

import subprocess
import sys
from typing import List

from remote_tmux.config import RemoteProfile
from remote_tmux.safety import check_command_safety, CommandSafetySuggestion
from remote_tmux.operation_log import get_logger


class RemoteTmuxManager:
    """Manages remote tmux sessions for AI-driven workflows."""

    RESERVED_WINDOWS = {"home"}
    SSH_CLEAR_FORWARDINGS = ["-o", "ClearAllForwardings=yes"]

    def __init__(self):
        """Initialize remote tmux manager."""
        self._last_operation_id = None

    def get_last_operation_id(self) -> str:
        """Get the operation ID of the last command sent.

        Returns:
            Operation ID for tracking, or None if no command sent yet
        """
        return self._last_operation_id

    def validate_task_name(self, task_name: str) -> None:
        """Validate task name is not reserved.

        Args:
            task_name: Task window name

        Raises:
            ValueError: If task name is reserved
        """
        if task_name in self.RESERVED_WINDOWS:
            raise ValueError(f"Task name '{task_name}' is reserved")

    def _build_ssh_command(
        self,
        profile: RemoteProfile,
        script: str,
        *,
        interactive: bool = False,
        batch_mode: bool = True,
    ) -> List[str]:
        """Build a consistent SSH command for remote tmux operations."""
        command = ["ssh", *self.SSH_CLEAR_FORWARDINGS]
        if interactive:
            command.append("-tt")
        if batch_mode:
            command.extend(["-o", "BatchMode=yes"])
        command.extend([profile.ssh_target, script.strip()])
        return command

    def build_open_command(self, profile: RemoteProfile) -> List[str]:
        """Build ssh command to open/attach managed tmux session.

        Args:
            profile: Remote profile configuration

        Returns:
            Command list for subprocess execution
        """
        script = f"""
set -e
SESSION="{profile.session_name}"
PROFILE="{profile.name}"
REPO="{profile.repo_path}"

apply_window_size_mode() {{
    if ! tmux set-option -t "$SESSION" -gq window-size latest 2>/dev/null; then
        tmux set-option -t "$SESSION" -gq window-size smallest
    fi
}}

# Check if session exists
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Attaching to existing session: $SESSION"
    apply_window_size_mode
    tmux attach -t "$SESSION"
else
    echo "Creating new managed session: $SESSION"
    # Create session with home window
    tmux new-session -d -s "$SESSION" -n home -c "$REPO"

    # Mark as chrono-managed
    tmux set-option -t "$SESSION" -q @chrono_managed 1
    tmux set-option -t "$SESSION" -q @chrono_profile "$PROFILE"
    apply_window_size_mode

    # Attach
    tmux attach -t "$SESSION"
fi
"""
        return self._build_ssh_command(profile, script, interactive=True, batch_mode=False)

    def build_status_command(self, profile: RemoteProfile) -> List[str]:
        """Build command to check session status.

        Args:
            profile: Remote profile configuration

        Returns:
            Command list for subprocess execution
        """
        script = f"""
SESSION="{profile.session_name}"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session: $SESSION (active)"
    tmux list-windows -t "$SESSION" -F "  #I: #W (#{{window_panes}} panes)"
else
    echo "Session: $SESSION (not running)"
fi
"""
        return self._build_ssh_command(profile, script)

    def build_list_tasks_command(self, profile: RemoteProfile) -> List[str]:
        """Build command to list task windows.

        Args:
            profile: Remote profile configuration

        Returns:
            Command list for subprocess execution
        """
        script = f"""
SESSION="{profile.session_name}"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux list-windows -t "$SESSION" -F "#I: #W"
else
    echo "Session not running"
    exit 1
fi
"""
        return self._build_ssh_command(profile, script)

    def build_new_task_command(self, profile: RemoteProfile, task_name: str) -> List[str]:
        """Build command to create new task window.

        Args:
            profile: Remote profile configuration
            task_name: Task window name

        Returns:
            Command list for subprocess execution
        """
        self.validate_task_name(task_name)
        script = f"""
set -e
SESSION="{profile.session_name}"
TASK="{task_name}"
REPO="{profile.repo_path}"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session not running. Use 'remote open' first."
    exit 1
fi

if tmux list-windows -t "$SESSION" -F "#W" | grep -q "^$TASK$"; then
    echo "Task window '$TASK' already exists"
    exit 1
fi

tmux new-window -t "$SESSION" -n "$TASK" -c "$REPO"
echo "Created task window: $TASK"
"""
        return self._build_ssh_command(profile, script)

    def build_switch_task_command(self, profile: RemoteProfile, task_name: str) -> List[str]:
        """Build command to switch to task window.

        Args:
            profile: Remote profile configuration
            task_name: Task window name

        Returns:
            Command list for subprocess execution
        """
        script = f"""
set -e
SESSION="{profile.session_name}"
TASK="{task_name}"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session not running"
    exit 1
fi

if ! tmux list-windows -t "$SESSION" -F "#W" | grep -q "^$TASK$"; then
    echo "Task window '$TASK' not found"
    exit 1
fi

tmux select-window -t "$SESSION:$TASK"
echo "Switched to: $TASK"
"""
        return self._build_ssh_command(profile, script)

    def build_send_command(
        self, profile: RemoteProfile, task_name: str, command: str, raw: bool = False
    ) -> List[str]:
        """Build command to send keys to task window.

        Args:
            profile: Remote profile configuration
            task_name: Task window name
            command: Command to send
            raw: If True, send command as-is; if False, auto-cd to repo first

        Returns:
            Command list for subprocess execution

        Note:
            This method checks command safety and prints suggestions to stderr.
            If command is unsafe (e.g., contains 'rm'), it will raise ValueError
            with suggestions for safer alternatives.
        """
        self.validate_task_name(task_name)

        # Check command safety
        is_safe, suggestions = check_command_safety(command)

        # Print suggestions if any
        if suggestions:
            print("\n" + "=" * 70, file=sys.stderr)
            print("命令安全检查", file=sys.stderr)
            print("=" * 70, file=sys.stderr)
            for suggestion in suggestions:
                print(f"\n{suggestion}", file=sys.stderr)
            print("=" * 70 + "\n", file=sys.stderr)

        # Block unsafe commands
        if not is_safe:
            error_suggestions = [s for s in suggestions if s.severity == "error"]
            raise ValueError(
                f"命令被安全检查拒绝。请使用建议的安全替代方案。\n" +
                "\n".join(str(s) for s in error_suggestions)
            )

        if raw:
            full_command = command
        else:
            full_command = f"cd {profile.repo_path} && {command}"

        # Log operation before execution
        logger = get_logger()
        operation_id = logger.log_operation(
            operation_type="send_command",
            profile=profile.name,
            command=full_command,
            metadata={
                "task_name": task_name,
                "raw": raw,
                "original_command": command,
                "safety_warnings": len([s for s in suggestions if s.severity == "warning"])
            }
        )

        # Store operation_id for later status update
        self._last_operation_id = operation_id

        script = f"""
set -e
SESSION='{profile.session_name}'
TASK='{task_name}'

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session not running"
    exit 1
fi

if ! tmux list-windows -t "$SESSION" -F "#W" | grep -q "^$TASK$"; then
    echo "Task window '$TASK' not found. Create it first with 'remote tasks new'."
    exit 1
fi

tmux send-keys -t "$SESSION:$TASK" "{full_command}" C-m
"""
        return self._build_ssh_command(profile, script)

    def build_capture_command(
        self, profile: RemoteProfile, task_name: str, lines: int = 120
    ) -> List[str]:
        """Build command to capture task window output.

        Args:
            profile: Remote profile configuration
            task_name: Task window name
            lines: Number of lines to capture

        Returns:
            Command list for subprocess execution
        """
        self.validate_task_name(task_name)
        script = f"""
SESSION="{profile.session_name}"
TASK="{task_name}"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session not running"
    exit 1
fi

if ! tmux list-windows -t "$SESSION" -F "#W" | grep -q "^$TASK$"; then
    echo "Task window '$TASK' not found"
    exit 1
fi

tmux capture-pane -t "$SESSION:$TASK" -p -S -{lines}
"""
        return self._build_ssh_command(profile, script)

    def build_close_task_command(self, profile: RemoteProfile, task_name: str) -> List[str]:
        """Build command to close task window.

        Args:
            profile: Remote profile configuration
            task_name: Task window name

        Returns:
            Command list for subprocess execution
        """
        self.validate_task_name(task_name)
        script = f"""
set -e
SESSION="{profile.session_name}"
TASK="{task_name}"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session not running"
    exit 1
fi

if ! tmux list-windows -t "$SESSION" -F "#W" | grep -q "^$TASK$"; then
    echo "Task window '$TASK' not found"
    exit 1
fi

tmux kill-window -t "$SESSION:$TASK"
echo "Closed task window: $TASK"
"""
        return self._build_ssh_command(profile, script)

    def execute(self, command: List[str], check: bool = True) -> subprocess.CompletedProcess:
        """Execute command and return result.

        Args:
            command: Command list
            check: If True, raise on non-zero exit

        Returns:
            CompletedProcess result
        """
        return subprocess.run(command, check=check, text=True, capture_output=True)

    def execute_interactive(self, command: List[str]) -> int:
        """Execute command interactively (for open/attach).

        Args:
            command: Command list

        Returns:
            Exit code
        """
        return subprocess.call(command)
