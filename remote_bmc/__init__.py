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
