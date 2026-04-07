"""Remote BMC Controller - Hardware-level server management."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict


@dataclass
class BMCConfig:
    """BMC configuration."""
    ip: str
    user: str
    password: str
    interface: str = "lanplus"


class BMCController:
    """BMC controller for hardware-level server management.

    Provides:
    - Power management (on/off/reset/cycle)
    - Status checking
    - Serial console access (future)
    """

    def __init__(self, config: BMCConfig):
        """Initialize BMC controller.

        Args:
            config: BMC configuration
        """
        self.config = config

    def _run_ipmitool(self, *args) -> subprocess.CompletedProcess:
        """Run ipmitool command.

        Args:
            *args: Arguments to pass to ipmitool

        Returns:
            CompletedProcess result
        """
        cmd = [
            "ipmitool",
            "-H", self.config.ip,
            "-U", self.config.user,
            "-E",  # Read password from IPMITOOL_PASSWORD env
            "-I", self.config.interface,
            *args
        ]

        env = os.environ.copy()
        env["IPMITOOL_PASSWORD"] = self.config.password

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env
        )

    def status(self) -> Dict[str, str]:
        """Get chassis status.

        Returns:
            Dictionary with status information
        """
        result = self._run_ipmitool("chassis", "status")

        if result.returncode != 0:
            return {
                "error": result.stderr.strip(),
                "reachable": "false"
            }

        # Parse output
        status = {"reachable": "true"}
        for line in result.stdout.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                status[key.strip()] = value.strip()

        return status

    def reset(self) -> bool:
        """Reset server (hard reset).

        Returns:
            True if successful
        """
        result = self._run_ipmitool("power", "reset")
        return result.returncode == 0

    def power_on(self) -> bool:
        """Power on server.

        Returns:
            True if successful
        """
        result = self._run_ipmitool("power", "on")
        return result.returncode == 0

    def power_off(self) -> bool:
        """Power off server.

        Returns:
            True if successful
        """
        result = self._run_ipmitool("power", "off")
        return result.returncode == 0

    def power_cycle(self) -> bool:
        """Power cycle server (off then on).

        Returns:
            True if successful
        """
        result = self._run_ipmitool("power", "cycle")
        return result.returncode == 0

    def is_reachable(self) -> bool:
        """Check if BMC is reachable.

        Returns:
            True if reachable
        """
        status = self.status()
        return status.get("reachable") == "true"


def load_bmc_config_from_env() -> Optional[BMCConfig]:
    """Load BMC config from environment variables.

    Returns:
        BMCConfig if all required vars are set, None otherwise
    """
    ip = os.getenv("CHRONO_BMC_IP")
    user = os.getenv("CHRONO_BMC_USER")
    password = os.getenv("CHRONO_BMC_PASSWORD") or os.getenv("IPMITOOL_PASSWORD")
    interface = os.getenv("CHRONO_BMC_INTERFACE", "lanplus")

    if not all([ip, user, password]):
        return None

    return BMCConfig(
        ip=ip,
        user=user,
        password=password,
        interface=interface
    )


def load_bmc_config_from_file(path: str | Path) -> Optional[BMCConfig]:
    """Load BMC config from a shell-style env file."""
    env_path = Path(path)
    if not env_path.exists():
        return None

    script = f"""
set -a
. "{env_path}"
python3 - <<'PY'
import json
import os

print(json.dumps({{
    "CHRONO_BMC_IP": os.getenv("CHRONO_BMC_IP"),
    "CHRONO_BMC_USER": os.getenv("CHRONO_BMC_USER"),
    "CHRONO_BMC_PASSWORD": os.getenv("CHRONO_BMC_PASSWORD"),
    "IPMITOOL_PASSWORD": os.getenv("IPMITOOL_PASSWORD"),
    "CHRONO_BMC_INTERFACE": os.getenv("CHRONO_BMC_INTERFACE"),
}}))
PY
"""
    result = subprocess.run(
        ["bash", "-lc", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    import json

    values = json.loads(result.stdout)

    ip = values.get("CHRONO_BMC_IP")
    user = values.get("CHRONO_BMC_USER")
    password = values.get("CHRONO_BMC_PASSWORD") or values.get("IPMITOOL_PASSWORD")
    interface = values.get("CHRONO_BMC_INTERFACE") or "lanplus"

    if not all([ip, user, password]):
        return None

    return BMCConfig(ip=ip, user=user, password=password, interface=interface)


def load_bmc_config(config_root: str | Path | None = None) -> Optional[BMCConfig]:
    """Load BMC config from env or the project's shared env file locations."""
    env_config = load_bmc_config_from_env()
    if env_config is not None:
        return env_config

    explicit_env_file = os.getenv("CHRONO_BMC_ENV_FILE")
    if explicit_env_file:
        file_config = load_bmc_config_from_file(explicit_env_file)
        if file_config is not None:
            return file_config

    if config_root is None:
        return None

    root = Path(config_root)
    search_roots = [root]
    if root.name == "config":
        search_roots.append(root.parent)

    for base in search_roots:
        for candidate in (
            base / "remote_tmux" / "bmc.env.local",
            base / "remote_tmux" / "bmc.env",
            base / "config" / "remote_tmux" / "bmc.env.local",
            base / "config" / "remote_tmux" / "bmc.env",
            base / "scripts" / ".bmc.env.local",
            base / "scripts" / ".bmc.env",
        ):
            file_config = load_bmc_config_from_file(candidate)
            if file_config is not None:
                return file_config

    return None
