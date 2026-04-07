"""High-level API for automated remote experiments with safety and logging."""

import time
from pathlib import Path
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from remote_tmux import RemoteTmuxManager, load_remote_profiles
from remote_tmux.operation_log import get_logger

if TYPE_CHECKING:
    from remote_server import RemoteGateway, ServiceStatus
else:
    # Import at runtime to avoid circular import
    RemoteGateway = None
    ServiceStatus = None


class RemoteExperimentRunner:
    """High-level runner for automated remote experiments.

    Features:
    - Command safety filtering (no rm allowed)
    - Operation logging before execution
    - Health checking and auto-recovery
    - Task isolation in tmux windows
    """

    def __init__(self, profile_name: str, config_root: Optional[Path] = None):
        """Initialize remote experiment runner.

        Args:
            profile_name: Remote profile name
            config_root: Optional config root directory
        """
        # Import here to avoid circular import
        from remote_server import RemoteGateway as RG, ServiceStatus as SS
        global RemoteGateway, ServiceStatus
        RemoteGateway = RG
        ServiceStatus = SS

        # Load profile
        if config_root is None:
            config_root = Path.home() / ".config"

        profiles = load_remote_profiles(config_root)
        if profile_name not in profiles:
            raise ValueError(f"Profile '{profile_name}' not found")

        self.profile = profiles[profile_name]
        self.profile_name = profile_name

        # Initialize managers
        self.tmux = RemoteTmuxManager()
        self.gateway = RemoteGateway(profile_name, config_root=str(config_root))
        self.logger = get_logger()

    def ensure_healthy(self) -> bool:
        """Ensure server is healthy before operations.

        Returns:
            True if healthy or recovered
        """
        report = self.gateway.check_health(force=True)

        if report.ssh_status == ServiceStatus.HEALTHY:
            return True

        print("⚠️  Server unhealthy, attempting recovery...")
        return self.gateway.ensure_healthy(auto_recover=True)

    def send_command(
        self,
        task_name: str,
        command: str,
        wait_for_completion: bool = False,
        timeout: int = 300
    ) -> Dict[str, Any]:
        """Send command to remote task window with safety checks.

        Args:
            task_name: Task window name
            command: Command to execute
            wait_for_completion: If True, wait for command to complete
            timeout: Timeout in seconds for completion wait

        Returns:
            Dict with operation_id, status, and optional output

        Raises:
            ValueError: If command is unsafe (e.g., contains 'rm')
            RuntimeError: If server is unhealthy or command fails
        """
        # Ensure server is healthy
        if not self.ensure_healthy():
            raise RuntimeError("Server is not healthy and recovery failed")

        # Build and execute command (this logs the operation and checks safety)
        try:
            cmd = self.tmux.build_send_command(self.profile, task_name, command)
            result = self.tmux.execute(cmd)

            operation_id = self.tmux.get_last_operation_id()

            if result.returncode == 0:
                self.logger.mark_completed(operation_id, "Command sent successfully")

                response = {
                    "operation_id": operation_id,
                    "status": "sent",
                    "task_name": task_name
                }

                if wait_for_completion:
                    # Wait and capture output
                    time.sleep(2)  # Give command time to start
                    output = self.capture_output(task_name, lines=100)
                    response["output"] = output
                    response["status"] = "completed"

                return response
            else:
                error_msg = result.stderr or "Unknown error"
                self.logger.mark_failed(operation_id, error_msg)
                raise RuntimeError(f"Failed to send command: {error_msg}")

        except ValueError as e:
            # Command rejected by safety check
            # Log the rejected command
            operation_id = self.logger.log_operation(
                operation_type="send_command_rejected",
                profile=self.profile_name,
                command=command,
                metadata={"task_name": task_name, "error": str(e)}
            )
            self.logger.mark_failed(operation_id, str(e))
            # Re-raise with safety suggestions visible to AI
            raise

    def capture_output(self, task_name: str, lines: int = 100) -> str:
        """Capture output from task window.

        Args:
            task_name: Task window name
            lines: Number of lines to capture

        Returns:
            Captured output
        """
        cmd = self.tmux.build_capture_command(self.profile, task_name, lines=lines)
        result = self.tmux.execute(cmd)

        if result.returncode == 0:
            return result.stdout
        else:
            return f"Failed to capture output: {result.stderr}"

    def create_task(self, task_name: str) -> bool:
        """Create a new task window.

        Args:
            task_name: Task window name

        Returns:
            True if created successfully
        """
        cmd = self.tmux.build_new_task_command(self.profile, task_name)
        result = self.tmux.execute(cmd)
        return result.returncode == 0

    def close_task(self, task_name: str) -> bool:
        """Close a task window.

        Args:
            task_name: Task window name

        Returns:
            True if closed successfully
        """
        cmd = self.tmux.build_close_task_command(self.profile, task_name)
        result = self.tmux.execute(cmd)
        return result.returncode == 0

    def run_experiment_sequence(
        self,
        task_name: str,
        commands: List[str],
        capture_after_each: bool = False
    ) -> List[Dict[str, Any]]:
        """Run a sequence of commands in a task window.

        Args:
            task_name: Task window name
            commands: List of commands to execute
            capture_after_each: If True, capture output after each command

        Returns:
            List of results for each command
        """
        results = []

        # Create task if it doesn't exist
        self.create_task(task_name)

        for i, command in enumerate(commands):
            print(f"[{i+1}/{len(commands)}] Executing: {command[:60]}...")

            try:
                result = self.send_command(
                    task_name,
                    command,
                    wait_for_completion=capture_after_each
                )
                results.append(result)
                print(f"  ✓ Completed")

            except Exception as e:
                print(f"  ✗ Failed: {e}")
                results.append({
                    "status": "failed",
                    "error": str(e),
                    "command": command
                })
                break

        return results

    def get_pending_operations(self) -> List[Dict[str, Any]]:
        """Get all pending operations.

        Returns:
            List of pending operations
        """
        return self.logger.get_pending_operations()

    def get_recent_operations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent operations.

        Args:
            limit: Maximum number of operations to return

        Returns:
            List of recent operations
        """
        return self.logger.get_recent_operations(limit=limit)
