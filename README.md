# NeoLog

**Real-time structured log viewer and system monitor for developers.**

`neolog` is a terminal UI (TUI) that tails any JSON log file and gives you live dashboards, trace drill-down, level filtering, system metrics, and one-key log export — all in your terminal.

---

## Install

```bash
pip install neolog
```

Or install from source:

```bash
git clone https://github.com/GauravPandit27/TUI-logs
cd TUI-logs
pip install -e .
```

---

## Usage

### View a log file

```bash
neolog app.log
neolog /var/log/myapp.log --max-lines 5000
```

### Use the structured logger in your app

```python
from neolog import get_logger, trace, set_trace_id

logger = get_logger("myapp", log_file="app.log", level="DEBUG")

logger.info("Server started", extra={"port": 8080})
logger.warning("High memory", extra={"usage_mb": 1200})

@trace(log_args=True, log_result=True)
def process_order(order_id: str):
    logger.info("Processing order", extra={"order_id": order_id})
    ...
```

Each request gets its own trace ID automatically:

```python
from neolog import set_trace_id

def handle_request():
    trace_id = set_trace_id()  # auto UUID4
    logger.info("Request started", extra={"route": "/api/users"})
    ...
```

All errors tied to the same `trace_id` appear together in the **Error Traces** panel — one click drills down to that request's full log chain.

---

## Screens & Key Bindings

### 📊 Main Dashboard (`health`)

| Key | Action |
|-----|--------|
| `1` | Show all log levels |
| `2` | Debug and above |
| `3` | Info and above |
| `4` | Warn and above |
| `5` | Error only |
| `p` | Pause / resume live stream |
| `f` or `/` | Focus search bar |
| `r` | Reset all filters |
| `x` | Export filtered logs to JSON |
| `m` | Open System Metrics screen |
| `q` | Quit |

**Search syntax:**
- `timeout` — full-text match anywhere in the log line
- `user_id:42` — field-value match (`key:value`)

Click any error trace in the sidebar to drill down.

### 🔍 Tracer Screen

Opened when you click an error trace. Shows all logs tied to that `trace_id`.

| Key | Action |
|-----|--------|
| `Escape` / `b` | Back to dashboard |
| `f` or `/` | Search |
| `r` | Reset filters |
| `c` | Clear view |

### 🖥️ System Metrics Screen

Opened with `m` from the dashboard. Updates every second.

- **CPU** — overall % + sparkline + per-core bars
- **Memory** — RAM used/total + swap with colour-coded bars
- **Disk** — all mounted partitions with usage bars
- **Network** — upload/download rates with sparklines, cumulative totals
- **Top Processes** — sorted by CPU, shows PID, name, CPU%, RAM, threads, status

---

## Log Format

NeoLog works with any newline-delimited JSON log. The `get_logger()` utility produces compatible output, but you can point it at any structured log file:

```json
{"level": "INFO", "timestamp": "2026-08-12T12:00:00+00:00", "message": "User logged in", "user_id": 42}
{"level": "ERROR", "timestamp": "2026-08-12T12:00:01+00:00", "message": "DB timeout", "trace_id": "abc-123"}
```

Non-JSON lines are shown as plain dimmed text — no crashes.

---

## `@trace` Decorator

Wraps any function to automatically log entry, exit, execution time, and exceptions:

```python
from neolog import trace

@trace(log_args=True, log_result=False)
def fetch_user(user_id: int) -> dict:
    ...  # automatically logged with execution_time_sec
```

Output:
```json
{"level": "DEBUG", "message": "Entering fetch_user", "function": "fetch_user", "func_args": ["42"]}
{"level": "DEBUG", "message": "Exiting fetch_user",  "function": "fetch_user", "execution_time_sec": 0.234}
```

---

## Publishing to PyPI

```bash
pip install build twine

# Build
python -m build

# Upload to TestPyPI first
twine upload --repository testpypi dist/*

# Upload to PyPI
twine upload dist/*
```

---

## Requirements

- Python 3.10+
- `textual >= 0.82.0`
- `rich >= 13.7.1`
- `psutil >= 5.9.0`

---

## License

MIT
