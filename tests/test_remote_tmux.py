import argparse
import importlib.util
import tempfile
import textwrap
import unittest
from pathlib import Path

# Import from package
import remote_tmux.safety as safety
from remote_tmux.config import RemoteProfile, load_remote_profiles
from remote_tmux.manager import RemoteTmuxManager
from remote_tmux.cli import add_remote_subparser


class RemoteTmuxConfigTests(unittest.TestCase):
    def test_load_profiles_prefers_local_file_and_applies_default_session_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_root = Path(tmpdir)
            config_dir = config_root / "remote_tmux"
            config_dir.mkdir(parents=True)
            (config_dir / "profiles.example.yaml").write_text(
                "profiles:\n  ignored:\n    ssh_target: should-not-load\n    repo_path: ~/ignored\n",
                encoding="utf-8",
            )
            (config_dir / "profiles.local.yaml").write_text(
                textwrap.dedent(
                    """\
                    profiles:
                      tsinghua:
                        ssh_target: Tsinghua
                        repo_path: ~/chrono-dsa
                    """
                ),
                encoding="utf-8",
            )

            profiles = load_remote_profiles(config_root)

            self.assertEqual(sorted(profiles), ["tsinghua"])
            profile = profiles["tsinghua"]
            self.assertEqual(profile.name, "tsinghua")
            self.assertEqual(profile.ssh_target, "Tsinghua")
            self.assertEqual(profile.repo_path, "~/chrono-dsa")
            self.assertEqual(profile.session_name, "chrono-ai-tsinghua")

    def test_load_profiles_reports_missing_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_root = Path(tmpdir)

            with self.assertRaises(FileNotFoundError) as ctx:
                load_remote_profiles(config_root)

            message = str(ctx.exception)
            self.assertIn("remote_tmux", message)


class RemoteTmuxCliTests(unittest.TestCase):
    def test_remote_cli_parses_capture_command(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_remote_subparser(subparsers)

        args = parser.parse_args(
            [
                "remote",
                "tasks",
                "capture",
                "build-kernel",
                "--profile",
                "tsinghua",
                "--lines",
                "80",
            ]
        )

        self.assertEqual(args.command, "remote")
        self.assertEqual(args.remote_area, "tasks")
        self.assertEqual(args.task_action, "capture")
        self.assertEqual(args.task_name, "build-kernel")
        self.assertEqual(args.profile, "tsinghua")
        self.assertEqual(args.lines, 80)


class RemoteTmuxManagerTests(unittest.TestCase):
    def test_build_open_command_bootstraps_managed_session(self) -> None:
        profile = RemoteProfile(
            name="tsinghua",
            ssh_target="Tsinghua",
            repo_path="~/chrono-dsa",
            session_name="chrono-ai-tsinghua",
        )

        manager = RemoteTmuxManager()
        command = manager.build_open_command(profile)

        self.assertEqual(command[:4], ["ssh", "-o", "ClearAllForwardings=yes", "-tt"])
        self.assertEqual(command[4], "Tsinghua")
        script = command[-1]
        self.assertIn("tmux new-session -d -s \"$SESSION\" -n home", script)
        self.assertIn("tmux set-option -t \"$SESSION\" -q @chrono_managed 1", script)
        self.assertIn("tmux set-option -t \"$SESSION\" -q @chrono_profile \"$PROFILE\"", script)
        self.assertIn("if ! tmux set-option -t \"$SESSION\" -gq window-size latest 2>/dev/null; then", script)
        self.assertIn("tmux set-option -t \"$SESSION\" -gq window-size smallest", script)
        self.assertIn("tmux attach -t \"$SESSION\"", script)

    def test_build_send_command_targets_task_window_and_auto_cd(self) -> None:
        profile = RemoteProfile(
            name="tsinghua",
            ssh_target="Tsinghua",
            repo_path="~/chrono-dsa",
            session_name="chrono-ai-tsinghua",
        )

        manager = RemoteTmuxManager()
        command = manager.build_send_command(profile, "build-kernel", "git status", raw=False)

        self.assertEqual(
            command[:6],
            ["ssh", "-o", "ClearAllForwardings=yes", "-o", "BatchMode=yes", "Tsinghua"],
        )
        script = command[-1]
        self.assertIn("TASK='build-kernel'", script)
        self.assertIn("cd ~/chrono-dsa && git status", script)
        self.assertIn("tmux send-keys -t \"$SESSION:$TASK\"", script)

    def test_build_close_command_rejects_home_window(self) -> None:
        manager = RemoteTmuxManager()

        with self.assertRaises(ValueError) as ctx:
            manager.validate_task_name("home")

        self.assertIn("reserved", str(ctx.exception))


class RemoteTmuxSafetyApiTests(unittest.TestCase):
    def test_safety_module_only_keeps_active_command_check_entrypoint(self) -> None:
        self.assertTrue(hasattr(safety, "check_command_safety"))
        self.assertFalse(hasattr(safety, "create_backup_command"))
        self.assertFalse(hasattr(safety, "create_safe_delete_command"))


if __name__ == "__main__":
    unittest.main()
