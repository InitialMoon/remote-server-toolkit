import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from remote_bmc import BMCConfig
from remote_server import OrchestrationReport, RemoteGateway, ServiceStatus
from remote_server.state_machine import (
    ConnectivityControlAdapter,
    ConnectivitySnapshot,
    ConnectivityStateId,
    ConnectivityStateMachine,
    DEFAULT_CONNECTIVITY_STATE_SPECS,
    DEFAULT_ORCHESTRATION_STATE_SPECS,
    DEFAULT_TASK_STATE_SPECS,
    DEFAULT_TMUX_STATE_SPECS,
    OrchestrationStateId,
    RemoteOrchestrationMachine,
    TaskControlAdapter,
    TaskSnapshot,
    TaskStateId,
    TaskStateMachine,
    TmuxControlAdapter,
    TmuxSnapshot,
    TmuxStateId,
    TmuxStateMachine,
)
from remote_tmux.config import RemoteProfile


class FakeConnectivityAdapter(ConnectivityControlAdapter):
    def __init__(self, snapshots, recover_host_snapshot=None, recover_ssh_snapshot=None):
        self.snapshots = list(snapshots)
        self.recover_host_snapshot = recover_host_snapshot
        self.recover_ssh_snapshot = recover_ssh_snapshot
        self.recover_host_calls = 0
        self.recover_ssh_calls = 0

    def probe_connectivity(self) -> ConnectivitySnapshot:
        if self.snapshots:
            return self.snapshots.pop(0)
        raise AssertionError("No connectivity snapshot available")

    def recover_host_power(self) -> ConnectivitySnapshot | None:
        self.recover_host_calls += 1
        return self.recover_host_snapshot

    def recover_ssh(self) -> ConnectivitySnapshot | None:
        self.recover_ssh_calls += 1
        return self.recover_ssh_snapshot


class FakeTmuxAdapter(TmuxControlAdapter):
    def __init__(self, snapshots, ensure_session_result=True, ensure_window_result=True):
        self.snapshots = list(snapshots)
        self.ensure_session_result = ensure_session_result
        self.ensure_window_result = ensure_window_result
        self.ensure_session_calls = 0
        self.ensure_window_calls = 0

    def probe_tmux(self) -> TmuxSnapshot:
        if self.snapshots:
            return self.snapshots.pop(0)
        raise AssertionError("No tmux snapshot available")

    def ensure_session(self) -> bool:
        self.ensure_session_calls += 1
        return self.ensure_session_result

    def ensure_window(self) -> bool:
        self.ensure_window_calls += 1
        return self.ensure_window_result


class FakeTaskAdapter(TaskControlAdapter):
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def inspect_task(self) -> TaskSnapshot:
        if self.snapshots:
            return self.snapshots.pop(0)
        raise AssertionError("No task snapshot available")


class ConnectivityStateMachineTests(unittest.TestCase):
    def test_connectivity_specs_are_documented(self) -> None:
        spec = DEFAULT_CONNECTIVITY_STATE_SPECS[ConnectivityStateId.SSH_UNAVAILABLE]

        self.assertEqual(spec.id, ConnectivityStateId.SSH_UNAVAILABLE)
        self.assertEqual(spec.layer, "connectivity")
        self.assertIn("recover_ssh", spec.allowed_actions)
        self.assertTrue(spec.summary)
        self.assertTrue(spec.evidence_description)
        self.assertTrue(spec.exit_description)

    def test_bmc_unreachable_directly_blocks_remote_connectivity(self) -> None:
        adapter = FakeConnectivityAdapter(
            [
                ConnectivitySnapshot(ssh_ok=None, bmc_ok=False, host_powered_on=None),
            ]
        )
        machine = ConnectivityStateMachine(adapter=adapter, auto_recover=True)

        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, ConnectivityStateId.REMOTE_UNAVAILABLE)
        self.assertEqual(machine.current_state, ConnectivityStateId.REMOTE_UNAVAILABLE)

    def test_powered_off_host_uses_bounded_recovery_action(self) -> None:
        adapter = FakeConnectivityAdapter(
            [
                ConnectivitySnapshot(ssh_ok=False, bmc_ok=True, host_powered_on=False),
            ],
            recover_host_snapshot=ConnectivitySnapshot(ssh_ok=True, bmc_ok=True, host_powered_on=True),
        )
        machine = ConnectivityStateMachine(adapter=adapter, auto_recover=True)

        machine.step()
        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, ConnectivityStateId.CHECKING_BMC)
        self.assertEqual(adapter.recover_host_calls, 1)

    def test_powered_on_host_continues_to_ssh_check(self) -> None:
        adapter = FakeConnectivityAdapter(
            [
                ConnectivitySnapshot(ssh_ok=False, bmc_ok=True, host_powered_on=True),
            ]
        )
        machine = ConnectivityStateMachine(adapter=adapter, auto_recover=True)

        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, ConnectivityStateId.CHECKING_SSH)

    def test_ssh_unavailable_uses_bounded_recovery_action(self) -> None:
        adapter = FakeConnectivityAdapter(
            [
                ConnectivitySnapshot(ssh_ok=False, bmc_ok=True, host_powered_on=True),
            ],
            recover_ssh_snapshot=ConnectivitySnapshot(ssh_ok=True, bmc_ok=True, host_powered_on=True),
        )
        machine = ConnectivityStateMachine(adapter=adapter, auto_recover=True)

        machine.step()
        machine.step()
        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, ConnectivityStateId.CHECKING_BMC)
        self.assertEqual(adapter.recover_ssh_calls, 1)


class TmuxStateMachineTests(unittest.TestCase):
    def test_tmux_specs_are_documented(self) -> None:
        spec = DEFAULT_TMUX_STATE_SPECS[TmuxStateId.WINDOW_MISSING]

        self.assertEqual(spec.id, TmuxStateId.WINDOW_MISSING)
        self.assertEqual(spec.layer, "tmux")
        self.assertIn("ensure_window", spec.allowed_actions)
        self.assertTrue(spec.summary)

    def test_missing_session_is_ensured_before_window_checks(self) -> None:
        adapter = FakeTmuxAdapter(
            [
                TmuxSnapshot(ssh_ok=True, session_exists=False, session_healthy=False, window_exists=False),
            ]
        )
        machine = TmuxStateMachine(adapter=adapter, auto_create=True)

        machine.step()
        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, TmuxStateId.ENSURING_SESSION)
        self.assertEqual(adapter.ensure_session_calls, 1)
        self.assertEqual(adapter.ensure_window_calls, 0)

    def test_window_missing_is_ensured_after_session_ready(self) -> None:
        adapter = FakeTmuxAdapter(
            [
                TmuxSnapshot(ssh_ok=True, session_exists=True, session_healthy=True, window_exists=False),
            ]
        )
        machine = TmuxStateMachine(adapter=adapter, auto_create=True)

        machine.step()
        machine.step()
        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, TmuxStateId.ENSURING_WINDOW)
        self.assertEqual(adapter.ensure_window_calls, 1)


class TaskStateMachineTests(unittest.TestCase):
    def test_task_specs_are_documented(self) -> None:
        spec = DEFAULT_TASK_STATE_SPECS[TaskStateId.RUNNING]

        self.assertEqual(spec.id, TaskStateId.RUNNING)
        self.assertEqual(spec.layer, "task")
        self.assertIn("inspect_task", spec.allowed_actions)
        self.assertTrue(spec.summary)

    def test_idle_task_state_is_distinct_from_tmux_window_state(self) -> None:
        adapter = FakeTaskAdapter(
            [
                TaskSnapshot(window_exists=True, command_active=False, exit_code=None),
            ]
        )
        machine = TaskStateMachine(adapter=adapter)

        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, TaskStateId.IDLE)

    def test_running_task_transitions_to_succeeded(self) -> None:
        adapter = FakeTaskAdapter(
            [
                TaskSnapshot(window_exists=True, command_active=True, exit_code=None),
                TaskSnapshot(window_exists=True, command_active=False, exit_code=0),
            ]
        )
        machine = TaskStateMachine(adapter=adapter)

        machine.step()
        machine.step()
        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, TaskStateId.SUCCEEDED)


class RemoteOrchestrationMachineTests(unittest.TestCase):
    def test_orchestration_specs_are_documented(self) -> None:
        spec = DEFAULT_ORCHESTRATION_STATE_SPECS[OrchestrationStateId.ENSURING_TMUX]

        self.assertEqual(spec.id, OrchestrationStateId.ENSURING_TMUX)
        self.assertEqual(spec.layer, "orchestration")
        self.assertIn("step_tmux", spec.allowed_actions)
        self.assertTrue(spec.summary)

    def test_orchestration_progresses_across_three_layers(self) -> None:
        connectivity = ConnectivityStateMachine(
            adapter=FakeConnectivityAdapter(
                [ConnectivitySnapshot(ssh_ok=True, bmc_ok=True, host_powered_on=True)]
            ),
            auto_recover=True,
        )
        tmux = TmuxStateMachine(
            adapter=FakeTmuxAdapter(
                [TmuxSnapshot(ssh_ok=True, session_exists=True, session_healthy=True, window_exists=True)]
            ),
            auto_create=True,
        )
        task = TaskStateMachine(
            adapter=FakeTaskAdapter(
                [TaskSnapshot(window_exists=True, command_active=False, exit_code=None)]
            )
        )
        machine = RemoteOrchestrationMachine(connectivity=connectivity, tmux=tmux, task=task)

        self.assertEqual(machine.step().to_state, OrchestrationStateId.ENSURING_CONNECTIVITY)
        self.assertEqual(machine.step().to_state, OrchestrationStateId.ENSURING_CONNECTIVITY)
        self.assertEqual(machine.step().to_state, OrchestrationStateId.ENSURING_CONNECTIVITY)
        self.assertEqual(machine.step().to_state, OrchestrationStateId.ENSURING_TMUX)
        self.assertEqual(machine.step().to_state, OrchestrationStateId.ENSURING_TMUX)
        self.assertEqual(machine.step().to_state, OrchestrationStateId.INSPECTING_TASK)
        self.assertEqual(machine.step().to_state, OrchestrationStateId.INSPECTING_TASK)
        self.assertEqual(machine.step().to_state, OrchestrationStateId.TASK_IDLE)

    def test_orchestration_stops_at_connectivity_blocker(self) -> None:
        connectivity = ConnectivityStateMachine(
            adapter=FakeConnectivityAdapter(
                [ConnectivitySnapshot(ssh_ok=None, bmc_ok=False, host_powered_on=None)],
            ),
            auto_recover=True,
        )
        tmux = TmuxStateMachine(adapter=FakeTmuxAdapter([]), auto_create=True)
        task = TaskStateMachine(adapter=FakeTaskAdapter([]))
        machine = RemoteOrchestrationMachine(connectivity=connectivity, tmux=tmux, task=task)

        machine.step()
        machine.step()
        transition = machine.step()

        self.assertEqual(transition.to_state, OrchestrationStateId.BLOCKED_CONNECTIVITY)


class RemoteGatewayInterfaceTests(unittest.TestCase):
    def _make_gateway(self) -> RemoteGateway:
        with TemporaryDirectory() as tmpdir:
            profile = RemoteProfile(
                name="tsinghua",
                ssh_target="Tsinghua_node198",
                repo_path="~/chrono-dsa",
                session_name="chrono-exp",
            )
            with patch("remote_server.load_remote_profiles", return_value={"tsinghua": profile}), patch(
                "remote_server.load_bmc_config", return_value=None
            ):
                gateway = RemoteGateway("tsinghua", config_root=Path(tmpdir))
        return gateway

    def test_get_status_summary_uses_connectivity_and_orchestration_states(self) -> None:
        gateway = self._make_gateway()
        report = OrchestrationReport(
            profile_name="tsinghua",
            ssh_target="Tsinghua_node198",
            orchestration_state=OrchestrationStateId.INSPECTING_TASK,
            connectivity_state=ConnectivityStateId.READY,
            tmux_state=TmuxStateId.WINDOW_READY,
            task_state=None,
            reason="Connectivity and tmux are ready; task inspection not requested.",
            details={"bmc_ok": True, "host_powered_on": True, "ssh_ok": True},
            history=("unknown->ensuring_connectivity:step_connectivity",),
            timestamp=123.0,
        )

        with patch.object(gateway, "get_orchestration_report", return_value=report):
            summary = gateway.get_status_summary()

        self.assertIn("Connectivity state: ready", summary)
        self.assertIn("Orchestration state: inspecting_task", summary)
        self.assertIn("Tmux state: window_ready", summary)

    def test_check_health_maps_ready_orchestration_to_healthy_services(self) -> None:
        gateway = self._make_gateway()
        report = OrchestrationReport(
            profile_name="tsinghua",
            ssh_target="Tsinghua_node198",
            orchestration_state=OrchestrationStateId.INSPECTING_TASK,
            connectivity_state=ConnectivityStateId.READY,
            tmux_state=TmuxStateId.WINDOW_READY,
            task_state=None,
            reason="Connectivity and tmux are ready; task inspection not requested.",
            details={"bmc_ok": True, "host_powered_on": True, "ssh_ok": True},
            history=(),
            timestamp=123.0,
        )

        with patch.object(gateway, "get_orchestration_report", return_value=report):
            health = gateway.check_health(force=True)

        self.assertEqual(health.ssh_status, ServiceStatus.HEALTHY)
        self.assertEqual(health.tmux_status, ServiceStatus.HEALTHY)
        self.assertEqual(health.bmc_status, ServiceStatus.HEALTHY)
        self.assertTrue(health.server_responsive)

    def test_ensure_healthy_uses_orchestration_readiness(self) -> None:
        gateway = self._make_gateway()
        report = OrchestrationReport(
            profile_name="tsinghua",
            ssh_target="Tsinghua_node198",
            orchestration_state=OrchestrationStateId.INSPECTING_TASK,
            connectivity_state=ConnectivityStateId.READY,
            tmux_state=TmuxStateId.WINDOW_READY,
            task_state=None,
            reason="Connectivity and tmux are ready; task inspection not requested.",
            details={"bmc_ok": True, "host_powered_on": True, "ssh_ok": True},
            history=(),
            timestamp=123.0,
        )

        with patch.object(gateway, "get_orchestration_report", return_value=report):
            self.assertTrue(gateway.ensure_healthy(auto_recover=True))

    def test_gateway_loads_bmc_config_from_project_env_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            scripts_dir = project_root / "scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / ".bmc.env.local").write_text(
                "\n".join(
                    [
                        'export CHRONO_BMC_IP="10.0.2.198"',
                        'export CHRONO_BMC_USER="admin"',
                        'export CHRONO_BMC_PASSWORD="secret"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            profile = RemoteProfile(
                name="tsinghua",
                ssh_target="Tsinghua_node198",
                repo_path="~/chrono-dsa",
                session_name="chrono-exp",
            )

            with patch("remote_server.load_remote_profiles", return_value={"tsinghua": profile}), patch(
                "remote_server.BMCController") as controller_cls:
                gateway = RemoteGateway("tsinghua", config_root=project_root)

        self.assertIsNotNone(gateway.bmc)
        controller_cls.assert_called_once()
        config = controller_cls.call_args.args[0]
        self.assertEqual(config, BMCConfig(ip="10.0.2.198", user="admin", password="secret", interface="lanplus"))

    def test_connectivity_without_bmc_config_reports_not_configured(self) -> None:
        gateway = self._make_gateway()

        with patch.object(gateway, "_probe_ssh_signal") as ssh_probe:
            report = gateway.get_connectivity_report(auto_recover=False)

        ssh_probe.assert_not_called()
        self.assertEqual(report.state, ConnectivityStateId.FAILED)
        self.assertEqual(report.details["bmc_detail"], "BMC is not configured.")
        self.assertEqual(report.reason, "BMC is not configured.")


if __name__ == "__main__":
    unittest.main()
