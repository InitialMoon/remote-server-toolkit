"""Operation logging for remote commands - persist before execution."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any


class OperationLogger:
    """Log remote operations before execution for recovery."""

    def __init__(self, log_dir: Optional[Path] = None):
        """Initialize operation logger.

        Args:
            log_dir: Directory to store operation logs (default: .claude/remote_ops)
        """
        if log_dir is None:
            log_dir = Path.home() / ".claude" / "remote_ops"

        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Current session log file
        self.session_log = self.log_dir / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"

    def log_operation(
        self,
        operation_type: str,
        profile: str,
        command: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Log an operation before execution.

        Args:
            operation_type: Type of operation (e.g., "send_command", "kernel_build")
            profile: Remote profile name
            command: Command to be executed
            metadata: Additional metadata

        Returns:
            Operation ID for tracking
        """
        operation_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        log_entry = {
            "operation_id": operation_id,
            "timestamp": datetime.now().isoformat(),
            "operation_type": operation_type,
            "profile": profile,
            "command": command,
            "metadata": metadata or {},
            "status": "pending"
        }

        # Append to session log (JSONL format)
        with open(self.session_log, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        return operation_id

    def mark_completed(self, operation_id: str, result: Optional[str] = None):
        """Mark operation as completed.

        Args:
            operation_id: Operation ID from log_operation
            result: Optional result summary
        """
        completion_entry = {
            "operation_id": operation_id,
            "timestamp": datetime.now().isoformat(),
            "status": "completed",
            "result": result
        }

        with open(self.session_log, "a") as f:
            f.write(json.dumps(completion_entry) + "\n")

    def mark_failed(self, operation_id: str, error: str):
        """Mark operation as failed.

        Args:
            operation_id: Operation ID from log_operation
            error: Error message
        """
        failure_entry = {
            "operation_id": operation_id,
            "timestamp": datetime.now().isoformat(),
            "status": "failed",
            "error": error
        }

        with open(self.session_log, "a") as f:
            f.write(json.dumps(failure_entry) + "\n")

    def get_pending_operations(self) -> list:
        """Get all pending operations from current session.

        Returns:
            List of pending operation entries
        """
        if not self.session_log.exists():
            return []

        operations = {}
        with open(self.session_log, "r") as f:
            for line in f:
                entry = json.loads(line)
                op_id = entry["operation_id"]

                if entry["status"] == "pending":
                    operations[op_id] = entry
                elif op_id in operations:
                    # Update with completion/failure status
                    operations[op_id]["status"] = entry["status"]
                    if "result" in entry:
                        operations[op_id]["result"] = entry["result"]
                    if "error" in entry:
                        operations[op_id]["error"] = entry["error"]

        # Return only truly pending operations
        return [op for op in operations.values() if op["status"] == "pending"]

    def get_recent_operations(self, limit: int = 10) -> list:
        """Get recent operations from current session.

        Args:
            limit: Maximum number of operations to return

        Returns:
            List of recent operation entries
        """
        if not self.session_log.exists():
            return []

        operations = {}
        with open(self.session_log, "r") as f:
            for line in f:
                entry = json.loads(line)
                op_id = entry["operation_id"]

                if op_id not in operations:
                    operations[op_id] = entry
                else:
                    # Merge status updates
                    operations[op_id].update({
                        k: v for k, v in entry.items()
                        if k not in ["operation_id", "timestamp"] or k == "status"
                    })

        # Sort by timestamp and return most recent
        sorted_ops = sorted(
            operations.values(),
            key=lambda x: x["timestamp"],
            reverse=True
        )
        return sorted_ops[:limit]


# Global logger instance
_logger: Optional[OperationLogger] = None


def get_logger() -> OperationLogger:
    """Get global operation logger instance."""
    global _logger
    if _logger is None:
        _logger = OperationLogger()
    return _logger
