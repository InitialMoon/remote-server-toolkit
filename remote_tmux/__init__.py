"""Remote Tmux Manager - AI-friendly remote tmux session manager."""

from .config import RemoteProfile, load_remote_profiles
from .manager import RemoteTmuxManager

__version__ = "0.1.0"
__all__ = ["RemoteProfile", "load_remote_profiles", "RemoteTmuxManager"]
