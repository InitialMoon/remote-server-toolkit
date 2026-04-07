"""Remote profile configuration loader."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import yaml


@dataclass
class RemoteProfile:
    """Remote lab profile configuration."""

    name: str
    ssh_target: str
    repo_path: str
    session_name: str
    bmc_reset_wait_seconds: int = 480
    ssh_probe_timeout_seconds: int = 10
    ssh_recovery_attempts: int = 10
    ssh_recovery_interval_seconds: int = 10


def load_remote_profiles(config_root: Path) -> Dict[str, RemoteProfile]:
    """Load remote profiles from config/remote_tmux/profiles.yaml or profiles.local.yaml.

    Args:
        config_root: Configuration root directory (e.g., ~/.config or project root)

    Returns:
        Dictionary mapping profile name to RemoteProfile

    Raises:
        FileNotFoundError: If no profile config exists
    """
    config_dir = config_root / "remote_tmux"
    local_config = config_dir / "profiles.local.yaml"
    default_config = config_dir / "profiles.yaml"
    example_config = config_dir / "profiles.example.yaml"

    # Try local first, then default, then example
    config_file = None
    if local_config.exists():
        config_file = local_config
    elif default_config.exists():
        config_file = default_config
    elif example_config.exists():
        config_file = example_config

    if config_file is None:
        raise FileNotFoundError(
            f"No profile config found in {config_dir}\n"
            f"Expected one of: profiles.local.yaml, profiles.yaml, profiles.example.yaml\n"
            f"Create {local_config} or {default_config} to get started."
        )

    with open(config_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    profiles = {}
    for name, config in data.get("profiles", {}).items():
        session_name = config.get("session_name", f"chrono-ai-{name}")
        profiles[name] = RemoteProfile(
            name=name,
            ssh_target=config["ssh_target"],
            repo_path=config["repo_path"],
            session_name=session_name,
            bmc_reset_wait_seconds=int(config.get("bmc_reset_wait_seconds", 480)),
            ssh_probe_timeout_seconds=int(config.get("ssh_probe_timeout_seconds", 10)),
            ssh_recovery_attempts=int(config.get("ssh_recovery_attempts", 10)),
            ssh_recovery_interval_seconds=int(config.get("ssh_recovery_interval_seconds", 10)),
        )

    return profiles
