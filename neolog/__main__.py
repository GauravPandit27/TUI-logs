"""
CLI entry point — invoked by `neolog <file>` and `python -m neolog <file>`.
"""
import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="neolog",
        description=(
            "NeoLog — Real-time structured log viewer and system monitor.\n\n"
            "Tail any JSON or plaintext log file with live dashboard, trace\n"
            "drill-down, level filters, system metrics, and export.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Key bindings (inside the TUI):\n"
            "  p          Pause / resume live stream\n"
            "  1-5        Level filter: All / Debug / Info / Warn / Error\n"
            "  f or /     Focus search bar\n"
            "  r          Reset all filters\n"
            "  x          Export filtered logs to JSON\n"
            "  m          Open system metrics screen\n"
            "  q          Quit\n\n"
            "Examples:\n"
            "  neolog app.log\n"
            "  neolog /var/log/syslog\n"
            "  neolog app.log --max-lines 5000\n"
        ),
    )
    parser.add_argument("file", help="Path to the log file to tail")
    parser.add_argument(
        "--max-lines",
        type=int,
        default=10_000,
        metavar="N",
        help="Maximum log lines kept in memory (default: 10000)",
    )
    args = parser.parse_args()

    from .app import LogViewerApp
    app = LogViewerApp(file_path=args.file, max_lines=args.max_lines)
    app.run()


if __name__ == "__main__":
    main()
