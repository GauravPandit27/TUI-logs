import argparse
import sys
from app import LogViewerApp

def main():
    parser = argparse.ArgumentParser(description="TUI Log Viewer & Analyzer")
    parser.add_argument("file", help="Path to the log file to tail")
    args = parser.parse_args()

    app = LogViewerApp(file_path=args.file)
    app.run()

if __name__ == "__main__":
    main()
