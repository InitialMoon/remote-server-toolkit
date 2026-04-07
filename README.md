# Remote Server Toolkit

Remote Server Toolkit is the reusable remote control layer behind Chrono's
`main.py remote` workflow. It is intentionally narrow in scope: it manages
remote connectivity, tmux orchestration, and task-window inspection. It does
not own experiment semantics, benchmark policy, or project-specific recovery
rules.

## Current Scope

The toolkit currently provides three building blocks:

- `remote_tmux`: builds and executes SSH + tmux commands for opening managed
  sessions and operating task windows.
- `remote_server`: exposes `RemoteGateway`, layered remote state machines, and
  heartbeat monitoring for readiness and recovery observation.
- `remote_bmc`: provides optional out-of-band power and connectivity recovery
  signals consumed by `RemoteGateway`.

The stable control path is:

```text
connectivity ready -> tmux window ready -> task inspection/result
```

## Public Entry Points

These are the supported public APIs today:

- `remote_tmux.RemoteTmuxManager`
- `remote_tmux.load_remote_profiles`
- `remote_server.RemoteGateway`
- `remote_server.HeartbeatMonitor`
- `remote_server.monitor_server_with_heartbeat`
- `remote_server.{Connectivity,Tmux,Task,RemoteOrchestration}StateMachine`
- `remote_server.{Connectivity,Tmux,Task,Orchestration}StateId`
- `remote_server.{Connectivity,Tmux,Task}Snapshot`

The toolkit no longer exports a high-level `RemoteExperimentRunner`. Experiment
sequencing belongs in the integrating project, not in the reusable remote
control layer.

## Layered State Machines

The toolkit models remote control as four small machines:

1. `connectivity`: BMC/SSH reachability and bounded host recovery
2. `tmux`: managed session and task-window readiness
3. `task`: whether a window is idle, running, succeeded, or failed
4. `orchestration`: thin external summary of which subsystem currently blocks
   or owns progress

The orchestration layer is intentionally small. It does not duplicate internal
tmux or task transitions; it only tells a caller where to look next.

## Configuration

Profiles are loaded from `config_root/remote_tmux/profiles.local.yaml` first.
A minimal example:

```yaml
profiles:
  tsinghua:
    ssh_target: Tsinghua_node198
    repo_path: ~/chrono-dsa
    session_name: chrono-ai-tsinghua
```

When used from Chrono, `config_root` is the repository's `config/` directory.

## Typical Usage

### Library

```python
from pathlib import Path

from remote_server import HeartbeatConfig, HeartbeatMonitor, RemoteGateway

config_root = Path("config")
gateway = RemoteGateway("tsinghua", config_root=config_root)

report = gateway.get_orchestration_report(auto_recover=False, auto_create=False)
print(report.orchestration_state.value)

monitor = HeartbeatMonitor(gateway, HeartbeatConfig(check_interval=10))
monitor.start()
```

### Low-level tmux command building

```python
from pathlib import Path

from remote_tmux import RemoteTmuxManager, load_remote_profiles

profiles = load_remote_profiles(Path("config"))
profile = profiles["tsinghua"]

manager = RemoteTmuxManager()
command = manager.build_send_command(profile, "build-kernel", "make -j32")
result = manager.execute(command)
```

## Integration Boundary

This toolkit is designed to be embedded by a project-level CLI. In Chrono, the
user-facing entrypoint is `scripts/remote_cli.py`, surfaced as `uv run main.py
remote ...`.

Recommended split:

- Toolkit: remote control primitives and reusable readiness logic
- Project: profile conventions, repo-specific task names, kernel build flow,
  experiment sequencing, result processing

## Development Notes

- `remote_tmux.safety.check_command_safety()` is the only active command-safety
  entrypoint. It blocks obvious destructive commands before `send`.
- `remote_server.__all__` intentionally exports the facade and state-machine
  types that callers consume directly. Internal contexts, transition records,
  and spec tables remain implementation details.
- Tests live in `tests/test_remote_state_machine.py` and
  `tests/test_remote_tmux.py`.
