"""Remote tmux CLI command handlers."""

import argparse
import sys
from pathlib import Path

from remote_tmux.config import load_remote_profiles
from remote_tmux.manager import RemoteTmuxManager


def add_remote_subparser(subparsers):
    """Add remote subcommand to main CLI parser.

    Args:
        subparsers: Subparsers object from argparse
    """
    p_remote = subparsers.add_parser(
        "remote",
        help="Manage remote tmux sessions for AI-driven experiments",
    )

    remote_subs = p_remote.add_subparsers(dest="remote_area", help="Remote operations")

    # profiles subcommand
    p_profiles = remote_subs.add_parser("profiles", help="Manage remote profiles")
    profile_subs = p_profiles.add_subparsers(dest="profile_action", help="Profile actions")

    p_profiles_list = profile_subs.add_parser("list", help="List available profiles")
    p_profiles_list.set_defaults(func=cmd_profiles_list)

    # open subcommand
    p_open = remote_subs.add_parser("open", help="Open/attach to remote tmux session")
    p_open.add_argument("--profile", required=True, help="Profile name")
    p_open.set_defaults(func=cmd_open)

    # status subcommand
    p_status = remote_subs.add_parser("status", help="Check remote session status")
    p_status.add_argument("--profile", required=True, help="Profile name")
    p_status.set_defaults(func=cmd_status)

    # tasks subcommand
    p_tasks = remote_subs.add_parser("tasks", help="Manage task windows")
    task_subs = p_tasks.add_subparsers(dest="task_action", help="Task actions")

    p_tasks_list = task_subs.add_parser("list", help="List task windows")
    p_tasks_list.add_argument("--profile", required=True, help="Profile name")
    p_tasks_list.set_defaults(func=cmd_tasks_list)

    p_tasks_new = task_subs.add_parser("new", help="Create new task window")
    p_tasks_new.add_argument("task_name", help="Task window name")
    p_tasks_new.add_argument("--profile", required=True, help="Profile name")
    p_tasks_new.set_defaults(func=cmd_tasks_new)

    p_tasks_switch = task_subs.add_parser("switch", help="Switch to task window")
    p_tasks_switch.add_argument("task_name", help="Task window name")
    p_tasks_switch.add_argument("--profile", required=True, help="Profile name")
    p_tasks_switch.set_defaults(func=cmd_tasks_switch)

    p_tasks_send = task_subs.add_parser("send", help="Send command to task window")
    p_tasks_send.add_argument("task_name", help="Task window name")
    p_tasks_send.add_argument("--profile", required=True, help="Profile name")
    p_tasks_send.add_argument("--raw", action="store_true", help="Send raw command (no auto-cd)")
    p_tasks_send.add_argument("command", nargs="+", help="Command to send")
    p_tasks_send.set_defaults(func=cmd_tasks_send)

    p_tasks_capture = task_subs.add_parser("capture", help="Capture task window output")
    p_tasks_capture.add_argument("task_name", help="Task window name")
    p_tasks_capture.add_argument("--profile", required=True, help="Profile name")
    p_tasks_capture.add_argument("--lines", type=int, default=120, help="Lines to capture")
    p_tasks_capture.set_defaults(func=cmd_tasks_capture)

    p_tasks_close = task_subs.add_parser("close", help="Close task window")
    p_tasks_close.add_argument("task_name", help="Task window name")
    p_tasks_close.add_argument("--profile", required=True, help="Profile name")
    p_tasks_close.set_defaults(func=cmd_tasks_close)


def get_config_paths() -> list[Path]:
    """Get list of config paths to search for profiles.

    Returns:
        List of paths in priority order (first found wins)
    """
    paths = []

    # 1. User-level config
    user_config = Path.home() / ".config" / "remote-tmux"
    if user_config.exists():
        paths.append(user_config)

    # 2. Project-level config (if running from a project)
    # Look for config/remote_tmux in current directory or parents
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        project_config = parent / "config" / "remote_tmux"
        if project_config.exists():
            paths.append(project_config)
            break

    return paths


def get_repo_root() -> Path:
    """Get configuration root directory.

    Searches in order:
    1. ~/.config/remote-tmux/
    2. <project_root>/config/remote_tmux/
    """
    paths = get_config_paths()
    if paths:
        return paths[0].parent  # Return parent to match load_remote_profiles expectation

    # Fallback to user config
    return Path.home() / ".config"


def cmd_profiles_list(args):
    """List available remote profiles."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if not profiles:
            print("No profiles configured")
            return

        print("Available remote profiles:")
        print("-" * 60)
        for name, profile in profiles.items():
            print(f"  {name}")
            print(f"    SSH target:   {profile.ssh_target}")
            print(f"    Repo path:    {profile.repo_path}")
            print(f"    Session name: {profile.session_name}")
            print()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_open(args):
    """Open/attach to remote tmux session."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if args.profile not in profiles:
            print(f"Error: Profile '{args.profile}' not found", file=sys.stderr)
            print(f"Available profiles: {', '.join(profiles.keys())}")
            sys.exit(1)

        profile = profiles[args.profile]
        manager = RemoteTmuxManager()
        command = manager.build_open_command(profile)

        print(f"Connecting to {profile.ssh_target}...")
        exit_code = manager.execute_interactive(command)
        sys.exit(exit_code)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    """Check remote session status."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if args.profile not in profiles:
            print(f"Error: Profile '{args.profile}' not found", file=sys.stderr)
            sys.exit(1)

        profile = profiles[args.profile]
        manager = RemoteTmuxManager()
        command = manager.build_status_command(profile)

        result = manager.execute(command, check=False)
        print(result.stdout)
        sys.exit(result.returncode)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_tasks_list(args):
    """List task windows."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if args.profile not in profiles:
            print(f"Error: Profile '{args.profile}' not found", file=sys.stderr)
            sys.exit(1)

        profile = profiles[args.profile]
        manager = RemoteTmuxManager()
        command = manager.build_list_tasks_command(profile)

        result = manager.execute(command, check=False)
        print(result.stdout)
        sys.exit(result.returncode)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_tasks_new(args):
    """Create new task window."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if args.profile not in profiles:
            print(f"Error: Profile '{args.profile}' not found", file=sys.stderr)
            sys.exit(1)

        profile = profiles[args.profile]
        manager = RemoteTmuxManager()
        command = manager.build_new_task_command(profile, args.task_name)

        result = manager.execute(command, check=False)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_tasks_switch(args):
    """Switch to task window."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if args.profile not in profiles:
            print(f"Error: Profile '{args.profile}' not found", file=sys.stderr)
            sys.exit(1)

        profile = profiles[args.profile]
        manager = RemoteTmuxManager()
        command = manager.build_switch_task_command(profile, args.task_name)

        result = manager.execute(command, check=False)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_tasks_send(args):
    """Send command to task window."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if args.profile not in profiles:
            print(f"Error: Profile '{args.profile}' not found", file=sys.stderr)
            sys.exit(1)

        profile = profiles[args.profile]
        manager = RemoteTmuxManager()
        command_str = " ".join(args.command)
        command = manager.build_send_command(profile, args.task_name, command_str, raw=args.raw)

        result = manager.execute(command, check=False)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_tasks_capture(args):
    """Capture task window output."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if args.profile not in profiles:
            print(f"Error: Profile '{args.profile}' not found", file=sys.stderr)
            sys.exit(1)

        profile = profiles[args.profile]
        manager = RemoteTmuxManager()
        command = manager.build_capture_command(profile, args.task_name, args.lines)

        result = manager.execute(command, check=False)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_tasks_close(args):
    """Close task window."""
    repo_root = get_repo_root()
    try:
        profiles = load_remote_profiles(repo_root)
        if args.profile not in profiles:
            print(f"Error: Profile '{args.profile}' not found", file=sys.stderr)
            sys.exit(1)

        profile = profiles[args.profile]
        manager = RemoteTmuxManager()
        command = manager.build_close_task_command(profile, args.task_name)

        result = manager.execute(command, check=False)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main entry point for remote-tmux CLI."""
    parser = argparse.ArgumentParser(
        prog="remote-tmux",
        description="AI-friendly remote tmux session manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  remote-tmux profiles list
  remote-tmux open --profile myserver
  remote-tmux tasks new build --profile myserver
  remote-tmux tasks send build --profile myserver -- make -j32
  remote-tmux tasks capture build --profile myserver --lines 120
        """,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    add_remote_subparser(subparsers)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
