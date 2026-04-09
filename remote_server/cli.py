"""Unified CLI for Remote Server Toolkit."""

import argparse
import signal
import sys

from remote_server import RemoteGateway, monitor_server_with_heartbeat
from remote_server.state_machine import ConnectivityStateId
from remote_tmux.cli import add_remote_subparser as add_tmux_subparser


def cmd_status(args):
    """Show server status with health check."""
    try:
        gateway = RemoteGateway(args.profile)
        print(gateway.get_status_summary())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_health(args):
    """Run health check."""
    try:
        gateway = RemoteGateway(args.profile)
        report = gateway.check_health(force=True)

        print(f"Health Check Report - {args.profile}")
        print("=" * 60)
        print(f"SSH:    {report.ssh_status.value}")
        print(f"Tmux:   {report.tmux_status.value}")
        print(f"BMC:    {report.bmc_status.value}")
        print(f"Server: {'Responsive' if report.server_responsive else 'Not responsive'}")
        print()
        print("Details:")
        for key, value in report.details.items():
            print(f"  {key}: {value}")

        sys.exit(0 if report.server_responsive else 1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_ensure(args):
    """Ensure server is healthy (with auto-recovery)."""
    try:
        gateway = RemoteGateway(args.profile)
        report = gateway.get_orchestration_report(auto_recover=True, auto_create=True)
        if report.orchestration_state.value == "inspecting_task":
            print(f"✓ Remote {args.profile} is ready")
            print(
                f"  connectivity={report.connectivity_state.value} "
                f"orchestration={report.orchestration_state.value}"
            )
            print(f"  reason={report.reason}")
            sys.exit(0)

        if report.connectivity_state == ConnectivityStateId.RECOVERING:
            print(f"… Remote {args.profile} is recovering")
            print(
                f"  connectivity={report.connectivity_state.value} "
                f"orchestration={report.orchestration_state.value}"
            )
            remaining = report.details.get("reboot_grace_remaining_seconds")
            if remaining is not None:
                print(f"  remaining={remaining}s")
            print(f"  reason={report.reason}")
            sys.exit(0)

        print(f"✗ Remote {args.profile} is not ready")
        print(
            f"  connectivity={report.connectivity_state.value} "
            f"orchestration={report.orchestration_state.value}"
        )
        print(f"  reason={report.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_monitor(args):
    """Optionally monitor server state changes with automatic heartbeat."""
    try:
        print(f"Starting optional heartbeat monitor for {args.profile}")
        print(f"Check interval: {args.interval}s")
        print("Press Ctrl+C to stop")
        print()

        monitor = monitor_server_with_heartbeat(
            args.profile,
            check_interval=args.interval,
            verbose=True
        )

        # Set up signal handler for graceful shutdown
        def signal_handler(sig, frame):
            print("\nStopping monitor...")
            monitor.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Keep main thread alive
        while True:
            signal.pause()

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main entry point for remote-server CLI."""
    parser = argparse.ArgumentParser(
        prog="remote-server",
        description="Unified remote server management toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check server status
  remote-server status --profile myserver

  # Run health check
  remote-server health --profile myserver

  # Ensure server is healthy (auto-recover)
  remote-server ensure --profile myserver

  # Use tmux commands
  remote-server tmux open --profile myserver
  remote-server tmux tasks new build --profile myserver
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Status command
    p_status = subparsers.add_parser("status", help="Show server status")
    p_status.add_argument("--profile", required=True, help="Profile name")
    p_status.set_defaults(func=cmd_status)

    # Health command
    p_health = subparsers.add_parser("health", help="Run health check")
    p_health.add_argument("--profile", required=True, help="Profile name")
    p_health.set_defaults(func=cmd_health)

    # Ensure command
    p_ensure = subparsers.add_parser("ensure", help="Ensure server is healthy")
    p_ensure.add_argument("--profile", required=True, help="Profile name")
    p_ensure.set_defaults(func=cmd_ensure)

    # Monitor command
    p_monitor = subparsers.add_parser(
        "monitor",
        help="Optional continuous observer for long waits and recovery windows",
    )
    p_monitor.add_argument("--profile", required=True, help="Profile name")
    p_monitor.add_argument("--interval", type=int, default=10, help="Check interval in seconds (default: 10)")
    p_monitor.set_defaults(func=cmd_monitor)

    # Tmux commands (nested)
    p_tmux = subparsers.add_parser("tmux", help="Tmux session management")
    tmux_subparsers = p_tmux.add_subparsers(dest="tmux_command")
    add_tmux_subparser(tmux_subparsers)

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
