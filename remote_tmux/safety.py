"""Command safety filter for remote operations."""

import re
from typing import Tuple


class CommandSafetySuggestion:
    """Suggestion for safer command alternative."""

    def __init__(self, original: str, suggestion: str, reason: str, severity: str = "error"):
        self.original = original
        self.suggestion = suggestion
        self.reason = reason
        self.severity = severity  # "error", "warning", "info"

    def __str__(self):
        icon = {"error": "🚫", "warning": "⚠️", "info": "ℹ️"}.get(self.severity, "•")
        return f"{icon} {self.reason}\n  原命令: {self.original}\n  建议: {self.suggestion}"


def check_command_safety(command: str) -> Tuple[bool, list]:
    """Check command safety and provide suggestions.

    Args:
        command: Raw command string

    Returns:
        Tuple of (is_safe, suggestions)
        - is_safe: False if command should be blocked
        - suggestions: List of CommandSafetySuggestion objects
    """
    suggestions = []

    # Check for rm commands
    if re.search(r'\brm\b', command):
        # Extract the file path if possible
        rm_match = re.search(r'\brm\s+(?:-[rf]+\s+)?(.+?)(?:\s|$|;|\||&)', command)
        file_path = rm_match.group(1) if rm_match else "<file>"

        suggestions.append(CommandSafetySuggestion(
            original=command,
            suggestion=f"mv {file_path} {file_path}.backup.$(date +%Y%m%d_%H%M%S)",
            reason="rm 命令被禁止。请使用 mv 移动到备份目录，而不是直接删除。",
            severity="error"
        ))
        return False, suggestions

    # Check for dangerous operations (warnings, not blocking)
    dangerous_patterns = [
        (r'\bdd\b.*if=',
         "dd 命令检测到。确保你有备份，并仔细检查 if= 和 of= 参数。",
         "使用 dd 前先用 'cp' 创建备份"),

        (r'\bmkfs\b',
         "mkfs 命令会销毁文件系统。确保你选择了正确的设备。",
         "先用 'lsblk' 确认设备，再执行 mkfs"),

        (r'\b>\s*/dev/',
         "写入 /dev/ 设备检测到。这是危险操作。",
         "确认设备路径正确，考虑先测试写入到 /tmp/"),

        (r'\bchmod\s+000\b',
         "chmod 000 会使文件完全不可访问。",
         "考虑使用 'chmod 400' 或其他权限"),

        (r'\bchown\s+root\b',
         "chown root 会改变文件所有者为 root。",
         "确认这是必要的，可能需要 sudo"),
    ]

    for pattern, reason, suggestion in dangerous_patterns:
        if re.search(pattern, command):
            suggestions.append(CommandSafetySuggestion(
                original=command,
                suggestion=suggestion,
                reason=reason,
                severity="warning"
            ))

    # All checks passed or only warnings
    is_safe = len([s for s in suggestions if s.severity == "error"]) == 0
    return is_safe, suggestions
