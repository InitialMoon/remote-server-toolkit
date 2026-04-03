# Integration Guide

## Using as a Git Submodule

### Adding to Your Project

```bash
# In your project root
git submodule add https://github.com/yourusername/remote-tmux-manager.git external/remote-tmux-manager
git submodule update --init --recursive
```

### Installing the Package

```bash
# Install in development mode
pip install -e external/remote-tmux-manager

# Or with uv
uv pip install -e external/remote-tmux-manager
```

### Integration Method 1: CLI Integration

Add remote commands to your existing CLI:

```python
# In your main.py
import argparse
from remote_tmux.cli import add_remote_subparser

def main():
    parser = argparse.ArgumentParser(description="Your project CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Add your existing commands
    # ...

    # Add remote-tmux commands
    add_remote_subparser(subparsers)

    args = parser.parse_args()
    # Handle commands...

if __name__ == "__main__":
    main()
```

Now your CLI will have:
```bash
python main.py remote profiles list
python main.py remote open --profile myserver
python main.py remote tasks new build --profile myserver
```

### Integration Method 2: Library Usage

Use remote-tmux as a library in your code:

```python
from pathlib import Path
from remote_tmux import RemoteTmuxManager, load_remote_profiles

# Load profiles from your project config
config_root = Path(__file__).parent / "config"
profiles = load_remote_profiles(config_root)

# Get a profile
profile = profiles["myserver"]

# Create manager
manager = RemoteTmuxManager()

# Build and execute commands
cmd = manager.build_send_command(profile, "build", "make -j32")
result = manager.execute(cmd)
```

### Configuration

Create `config/remote_tmux/profiles.local.yaml` in your project:

```yaml
profiles:
  myserver:
    ssh_target: user@server.example.com
    repo_path: ~/my-project
    session_name: my-ai-session
```

Add to `.gitignore`:
```
config/remote_tmux/profiles.local.yaml
```

### Updating the Submodule

```bash
# Update to latest version
git submodule update --remote external/remote-tmux-manager

# Commit the update
git add external/remote-tmux-manager
git commit -m "Update remote-tmux-manager submodule"
```

## Using as a Standalone Package

### Installation from PyPI (when published)

```bash
pip install remote-tmux-manager
```

### Configuration

Create `~/.config/remote-tmux/profiles.yaml`:

```yaml
profiles:
  server1:
    ssh_target: user@server1.example.com
    repo_path: ~/project1

  server2:
    ssh_target: user@server2.example.com
    repo_path: ~/project2
```

### Usage

```bash
# Use the standalone CLI
remote-tmux profiles list
remote-tmux open --profile server1
remote-tmux tasks new build --profile server1
remote-tmux tasks send build --profile server1 -- make -j32
```

## Example: Chrono-DSA Integration

The original Chrono-DSA project uses this as a submodule:

```bash
# Project structure
chrono-dsa/
├── external/
│   └── remote-tmux-manager/  # Git submodule
├── config/
│   └── remote_tmux/
│       ├── profiles.example.yaml
│       └── profiles.local.yaml  # Gitignored
├── main.py  # Integrates remote commands
└── ...
```

In `main.py`:
```python
from remote_tmux.cli import add_remote_subparser

# ... existing code ...

# Add remote commands
add_remote_subparser(subparsers)
```

Now the project has:
```bash
uv run main.py remote open --profile tsinghua
uv run main.py remote tasks new build-kernel --profile tsinghua
```

## API Reference

### RemoteProfile

```python
@dataclass
class RemoteProfile:
    name: str
    ssh_target: str
    repo_path: str
    session_name: str
```

### load_remote_profiles

```python
def load_remote_profiles(config_root: Path) -> Dict[str, RemoteProfile]:
    """Load profiles from config_root/remote_tmux/profiles.yaml"""
```

### RemoteTmuxManager

```python
class RemoteTmuxManager:
    def build_open_command(self, profile: RemoteProfile) -> List[str]:
        """Build command to open/attach session"""

    def build_status_command(self, profile: RemoteProfile) -> List[str]:
        """Build command to check session status"""

    def build_send_command(
        self, profile: RemoteProfile, task_name: str,
        command: str, raw: bool = False
    ) -> List[str]:
        """Build command to send keys to task window"""

    def build_capture_command(
        self, profile: RemoteProfile, task_name: str,
        lines: int = 120
    ) -> List[str]:
        """Build command to capture task output"""

    def execute(self, command: List[str]) -> subprocess.CompletedProcess:
        """Execute command and return result"""

    def execute_interactive(self, command: List[str]) -> int:
        """Execute command interactively"""
```

## Troubleshooting

### Submodule not found

```bash
git submodule update --init --recursive
```

### Import errors

```bash
# Reinstall in development mode
pip install -e external/remote-tmux-manager
```

### Config not found

Check that you have one of:
- `~/.config/remote-tmux/profiles.yaml`
- `<project>/config/remote_tmux/profiles.local.yaml`
- `<project>/config/remote_tmux/profiles.yaml`
