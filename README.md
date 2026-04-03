# Remote Tmux Manager

AI-friendly remote tmux session manager for distributed experiments and long-running tasks.

## Features

- **Persistent Sessions**: Each profile maps to a long-lived tmux session that survives disconnections
- **Task Isolation**: Manage multiple tasks in separate tmux windows
- **AI-Friendly**: Non-interactive `send` and `capture` commands for remote automation
- **Multiple Access Methods**:
  - Local CLI for status checking and output capture
  - Interactive tmux attachment
  - Remote manual access
- **Safe Design**: Protected home window, session tagging, reserved window name validation

## Installation

### As a Python package

```bash
pip install remote-tmux-manager
```

### As a git submodule

```bash
# In your project root
git submodule add https://github.com/yourusername/remote-tmux-manager.git external/remote-tmux-manager
git submodule update --init --recursive

# Install in development mode
pip install -e external/remote-tmux-manager
```

## Quick Start

1. Create a profile configuration:

```bash
mkdir -p ~/.config/remote-tmux
cp examples/profiles.example.yaml ~/.config/remote-tmux/profiles.yaml
```

2. Edit `~/.config/remote-tmux/profiles.yaml`:

```yaml
profiles:
  myserver:
    ssh_target: user@server.example.com
    repo_path: ~/my-project
    session_name: my-ai-session  # optional
```

3. Connect to remote session:

```bash
remote-tmux open --profile myserver
```

4. Manage tasks:

```bash
# Create task window
remote-tmux tasks new build --profile myserver

# Send command
remote-tmux tasks send build --profile myserver -- make -j32

# Capture output
remote-tmux tasks capture build --profile myserver --lines 120

# Close task
remote-tmux tasks close build --profile myserver
```

## Usage as a Library

```python
from pathlib import Path
from remote_tmux import RemoteTmuxManager, load_remote_profiles

# Load profiles
profiles = load_remote_profiles(Path.home() / ".config" / "remote-tmux")
profile = profiles["myserver"]

# Create manager
manager = RemoteTmuxManager()

# Build commands
open_cmd = manager.build_open_command(profile)
send_cmd = manager.build_send_command(profile, "build", "make -j32")
capture_cmd = manager.build_capture_command(profile, "build", lines=120)

# Execute
manager.execute_interactive(open_cmd)
result = manager.execute(capture_cmd)
print(result.stdout)
```

## Integration with Existing Projects

### Method 1: As a submodule with CLI integration

```python
# In your main.py
from remote_tmux.cli import add_remote_subparser

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers()

# Add remote commands
add_remote_subparser(subparsers)
```

### Method 2: As a library

```python
# In your project
from remote_tmux import RemoteTmuxManager, RemoteProfile

profile = RemoteProfile(
    name="myserver",
    ssh_target="user@server.example.com",
    repo_path="~/project",
    session_name="my-session"
)

manager = RemoteTmuxManager()
# Use manager methods...
```

## Configuration

### Profile Configuration

Profiles are loaded from:
1. `~/.config/remote-tmux/profiles.yaml` (user-level)
2. `<project_root>/config/remote-tmux/profiles.local.yaml` (project-level, gitignored)
3. Custom path via `load_remote_profiles(custom_path)`

### Profile Schema

```yaml
profiles:
  <profile_name>:
    ssh_target: <SSH target from ~/.ssh/config or user@host>
    repo_path: <remote repository path>
    session_name: <optional, defaults to "chrono-ai-<profile_name>">
```

## CLI Commands

```bash
# Profile management
remote-tmux profiles list

# Session management
remote-tmux open --profile <name>
remote-tmux status --profile <name>

# Task management
remote-tmux tasks list --profile <name>
remote-tmux tasks new <task> --profile <name>
remote-tmux tasks switch <task> --profile <name>
remote-tmux tasks send <task> --profile <name> -- <command>
remote-tmux tasks capture <task> --profile <name> [--lines N]
remote-tmux tasks close <task> --profile <name>
```

## Architecture

### Session Model

- Each profile → one long-lived tmux session
- Session naming: `chrono-ai-<profile>` (customizable)
- Session tagging: `@chrono_managed=1`, `@chrono_profile=<name>`
- `home` window: landing zone for manual operations (protected from AI)
- Task windows: created on-demand for specific tasks

### Security Features

- **Home window protection**: AI cannot send commands to `home` window
- **Session tagging**: Only manages sessions it created
- **Reserved names**: Validates task names against reserved list

### Command Modes

- **Interactive** (open): `ssh -tt <target> <script>`
- **Non-interactive** (others): `ssh -o BatchMode=yes <target> <script>`

## Development

```bash
# Clone repository
git clone https://github.com/yourusername/remote-tmux-manager.git
cd remote-tmux-manager

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run linter
ruff check .
```

## Use Cases

### AI-Driven Remote Compilation

```bash
remote-tmux tasks new build --profile server
remote-tmux tasks send build --profile server -- "cd kernel && make -j32"
# Wait...
remote-tmux tasks capture build --profile server --lines 200
```

### Parallel Task Management

```bash
# Create multiple task windows
remote-tmux tasks new build --profile server
remote-tmux tasks new test --profile server
remote-tmux tasks new monitor --profile server

# Run tasks in parallel
remote-tmux tasks send build --profile server -- make -j32
remote-tmux tasks send test --profile server -- pytest
remote-tmux tasks send monitor --profile server -- htop

# Capture outputs separately
remote-tmux tasks capture build --profile server
remote-tmux tasks capture test --profile server
```

### Interactive Debugging

```bash
# User attaches interactively
remote-tmux open --profile server

# Inside tmux:
# - Ctrl-b w: list all windows
# - Ctrl-b 0-9: switch windows
# - Ctrl-b d: detach
```

## Tmux Basics

| Shortcut | Action |
|----------|--------|
| `Ctrl-b c` | Create new window |
| `Ctrl-b w` | Window list (visual picker) |
| `Ctrl-b n/p` | Next/previous window |
| `Ctrl-b 0-9` | Jump to window |
| `Ctrl-b d` | Detach session |

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new features
4. Ensure all tests pass
5. Submit a pull request

## Changelog

### 0.1.0 (2026-04-03)

- Initial release
- Profile-based configuration
- Session and task management
- CLI and Python API
- Complete test coverage
