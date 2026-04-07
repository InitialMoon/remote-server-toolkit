# Integration Guide

This toolkit is meant to sit under a project-specific remote CLI. The project
owns user-facing workflow semantics; the toolkit owns reusable remote control.

## Recommended Architecture

Keep the split explicit:

- project CLI: commands such as `status`, `ensure`, `monitor`, `open`,
  `tasks send`, `tasks capture`
- toolkit facade: `RemoteGateway`
- toolkit transport layer: `RemoteTmuxManager`
- toolkit monitoring layer: `HeartbeatMonitor`

In Chrono this looks like:

```text
main.py remote -> scripts/remote_cli.py -> remote_server / remote_tmux
```

## Project-Side Integration

### 1. Install the submodule

```bash
git submodule update --init --recursive
uv pip install -e external/remote-server-toolkit
```

### 2. Provide profile config

Create `config/remote_tmux/profiles.local.yaml` in the integrating project:

```yaml
profiles:
  tsinghua:
    ssh_target: Tsinghua_node198
    repo_path: ~/chrono-dsa
    session_name: chrono-ai-tsinghua
```

### 3. Build a project-local gateway

```python
from pathlib import Path

from remote_server import RemoteGateway

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_gateway(profile: str) -> RemoteGateway:
    return RemoteGateway(profile, config_root=PROJECT_ROOT / "config")
```

This keeps profile resolution pinned to the repository config tree instead of
silently drifting to a global user config.

### 4. Use tmux commands through project wrappers

```python
from remote_tmux.manager import RemoteTmuxManager

manager = RemoteTmuxManager()
command = manager.build_send_command(profile, "build-kernel", "make -j32")
result = manager.execute(command, check=False)
```

The project wrapper remains responsible for:

- choosing repo-specific task names
- deciding whether to auto-`cd` into the repo
- presenting user-facing error messages
- deciding when `raw=True` is acceptable

## What The Toolkit Should Own

- SSH/BMC readiness probes
- bounded connectivity recovery
- tmux session/window creation and inspection
- task-window state inspection
- heartbeat event streaming
- destructive-command blocking before tmux `send`

## What The Toolkit Should Not Own

- experiment sequencing
- benchmark-specific policies
- kernel version expectations
- result parsing or processing
- project-specific naming conventions

The removed `RemoteExperimentRunner` is the concrete example of this boundary:
it mixed reusable remote control with higher-level workflow assumptions and was
not part of the current stable control path.

## Current Stable APIs

Use these as the supported integration surface:

```python
from remote_server import (
    ConnectivityStateId,
    HeartbeatConfig,
    HeartbeatMonitor,
    OrchestrationStateId,
    RemoteGateway,
    TmuxStateId,
)
from remote_tmux import RemoteTmuxManager, load_remote_profiles
```

If deeper inspection of the state machines is needed, import directly from
`remote_server.state_machine` rather than relying on broad top-level re-exports.

## Chrono Example

Chrono's stable remote path is:

```bash
uv run main.py remote profiles list
uv run main.py remote status --profile tsinghua
uv run main.py remote ensure --profile tsinghua
uv run main.py remote open --profile tsinghua
uv run main.py remote tasks new build-kernel --profile tsinghua
uv run main.py remote tasks send build-kernel --profile tsinghua -- "make -j32"
uv run main.py remote tasks capture build-kernel --profile tsinghua --lines 80
uv run main.py remote monitor --profile tsinghua --interval 10
```

That command set is the reference integration target. Anything beyond it should
be justified by a real project requirement, not by keeping legacy abstractions
alive.
