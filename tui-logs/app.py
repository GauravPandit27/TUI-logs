import json
import os
import time
from collections import deque
from datetime import datetime
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, RichLog, Static, ListView, ListItem, Label, Sparkline, Input, Button
from textual.reactive import reactive
from textual.screen import Screen
from textual.message import Message
# pyrefly: ignore [missing-import]
from textual.worker import get_current_worker

from highlighter import parse_json_log, format_log_line

# ── Alert thresholds ────────────────────────────────────────────────────────
ALERT_ERROR_THRESHOLD = 3   # errors/sec to trigger red border
ALERT_LATENCY_MS      = 500 # avg ms to trigger amber border

# ── Level filter helpers ─────────────────────────────────────────────────────
LEVEL_ORDER  = {"DEBUG": 0, "INFO": 1, "WARN": 2, "WARNING": 2, "ERROR": 3, "UNKNOWN": -1}
LEVEL_LABELS = ["ALL", "DEBUG", "INFO", "WARN", "ERROR"]


# ════════════════════════════════════════════════════════════════════════════
# LogStore
# ════════════════════════════════════════════════════════════════════════════

class LogStore:
    def __init__(self, max_size: int = 10000):
        self.start_time   = time.time()
        self.max_size     = max_size
        self.logs         = deque(maxlen=max_size)
        self.error_traces     = []
        self.error_traces_set = set()

        # Dashboard metrics
        self.total_logs         = 0
        self.total_errors       = 0
        self.logs_per_sec       = deque([0] * 60, maxlen=60)
        self.errors_per_sec     = deque([0] * 60, maxlen=60)
        self._current_sec_logs   = 0
        self._current_sec_errors = 0

        # Latency tracking
        self.recent_latencies = deque(maxlen=100)

    def add_log(self, raw_line: str, log_dict: dict, level: str, rich_text) -> None:
        log_entry = {
            "raw":       raw_line,
            "dict":      log_dict,
            "level":     level,
            "rich_text": rich_text,
        }
        self.logs.append(log_entry)
        self.total_logs          += 1
        self._current_sec_logs   += 1

        trace_id = log_dict.get("trace_id") if log_dict else None

        if level == "ERROR":
            self.total_errors         += 1
            self._current_sec_errors  += 1
            if trace_id and trace_id not in self.error_traces_set:
                self.error_traces_set.add(trace_id)
                self.error_traces.insert(0, trace_id)

        if log_dict and "execution_time_sec" in log_dict:
            try:
                self.recent_latencies.append(float(log_dict["execution_time_sec"]))
            except (ValueError, TypeError):
                pass

    def tick_second(self) -> None:
        self.logs_per_sec.append(self._current_sec_logs)
        self.errors_per_sec.append(self._current_sec_errors)
        self._current_sec_logs   = 0
        self._current_sec_errors = 0

    def get_avg_latency(self) -> float:
        if not self.recent_latencies:
            return 0.0
        return sum(self.recent_latencies) / len(self.recent_latencies)

    def get_uptime_str(self) -> str:
        delta = int(time.time() - self.start_time)
        m, s = divmod(delta, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


# ════════════════════════════════════════════════════════════════════════════
# MetricPanel + HealthDashboard
# ════════════════════════════════════════════════════════════════════════════

class MetricPanel(Vertical):
    def __init__(self, title: str, sparkline_color: str, id: str):
        super().__init__(id=id)
        self.panel_title      = title
        self.sparkline_color  = sparkline_color

    def compose(self) -> ComposeResult:
        yield Label(f"[b]{self.panel_title}[/b]", classes="metric-title")
        yield Label("0", classes="metric-value", id=f"{self.id}-val")
        yield Sparkline(data=[0] * 60, summary_function=max, id=f"{self.id}-spark")

    def on_mount(self) -> None:
        self.query_one(f"#{self.id}-spark", Sparkline).styles.tint = self.sparkline_color


class HealthDashboard(Horizontal):
    """Live statistics bar with sparklines and alert state."""

    def compose(self) -> ComposeResult:
        yield MetricPanel("📈 Log Volume (req/s)", "#a6e3a1", "metric-volume")
        yield MetricPanel("⚠️ Error Rate (err/s)", "#f38ba8", "metric-errors")

        with Vertical(id="metric-latency"):
            yield Label("[b]⏱️ Avg Latency[/b] (last 100)", classes="metric-title")
            yield Label("0.00ms", classes="metric-value", id="latency-val")

        with Vertical(id="metric-uptime"):
            yield Label("[b]⏳ Uptime[/b]", classes="metric-title")
            yield Label("00:00:00", classes="metric-value", id="uptime-val")

    def update_metrics(self, store: LogStore) -> None:
        self.query_one("#metric-volume-val", Label).update(
            f"{store.logs_per_sec[-1]}  [dim](Total: {store.total_logs})[/dim]"
        )

        err_color = "#f38ba8" if store.errors_per_sec[-1] > 0 else "#cdd6f4"
        self.query_one("#metric-errors-val", Label).update(
            f"[{err_color}]{store.errors_per_sec[-1]}[/{err_color}]  [dim](Total: {store.total_errors})[/dim]"
        )

        latency   = store.get_avg_latency() * 1000
        lat_color = "#f38ba8" if latency > 500 else ("#f9e2af" if latency > 200 else "#a6e3a1")
        self.query_one("#latency-val", Label).update(f"[{lat_color}]{latency:.2f}ms[/{lat_color}]")
        self.query_one("#uptime-val", Label).update(f"[b]{store.get_uptime_str()}[/b]")

        self.query_one("#metric-volume-spark", Sparkline).data = list(store.logs_per_sec)
        self.query_one("#metric-errors-spark", Sparkline).data = list(store.errors_per_sec)

    def set_alert_state(self, error_alert: bool, latency_alert: bool) -> None:
        """Flash dashboard border based on alert conditions."""
        if error_alert:
            self.add_class("alert-error")
            self.remove_class("alert-latency")
        elif latency_alert:
            self.add_class("alert-latency")
            self.remove_class("alert-error")
        else:
            self.remove_class("alert-error")
            self.remove_class("alert-latency")


# ════════════════════════════════════════════════════════════════════════════
# LevelFilterBar
# ════════════════════════════════════════════════════════════════════════════

class LevelFilterBar(Horizontal):
    """Horizontal strip of toggle buttons for log-level filtering."""

    class FilterChanged(Message):
        def __init__(self, level: str) -> None:
            super().__init__()
            self.level = level

    def compose(self) -> ComposeResult:
        yield Static("FILTER:", classes="filter-bar-label")
        for level in LEVEL_LABELS:
            extra = " level-btn-active" if level == "ALL" else ""
            yield Button(
                level,
                id=f"lvl-btn-{level.lower()}",
                classes=f"level-btn level-btn-{level.lower()}{extra}",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()  # don't bubble up to HealthScreen
        btn_id = event.button.id or ""
        if btn_id.startswith("lvl-btn-"):
            level = btn_id.replace("lvl-btn-", "").upper()
            self._set_active(level)
            self.post_message(self.FilterChanged(level))

    def set_level(self, level: str) -> None:
        """Called by hotkeys — updates visuals only (no extra message)."""
        self._set_active(level)

    def _set_active(self, active_level: str) -> None:
        for btn in self.query(".level-btn"):
            btn.remove_class("level-btn-active")
        try:
            self.query_one(f"#lvl-btn-{active_level.lower()}", Button).add_class("level-btn-active")
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ════════════════════════════════════════════════════════════════════════════

class TraceItem(ListItem):
    def __init__(self, trace_id: str):
        super().__init__()
        self.trace_id = trace_id

    def compose(self) -> ComposeResult:
        yield Label(f"🎯 {self.trace_id[:8]}...", classes="trace-label")


# ════════════════════════════════════════════════════════════════════════════
# HealthScreen  (main dashboard)
# ════════════════════════════════════════════════════════════════════════════

class HealthScreen(Screen):
    """The main dashboard screen."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "focus_search", "Search"),
        ("/", "focus_search", "Search"),
        ("r", "reset_filter", "Reset All"),
        ("p", "toggle_pause", "Pause/Resume"),
        ("x", "export_logs", "Export"),
        ("1", "set_level('ALL')",   "All"),
        ("2", "set_level('DEBUG')", "Debug+"),
        ("3", "set_level('INFO')",  "Info+"),
        ("4", "set_level('WARN')",  "Warn+"),
        ("5", "set_level('ERROR')", "Error"),
    ]

    active_search_query  = reactive("")
    active_level_filter  = reactive("ALL")
    is_paused            = reactive(False)

    # ── Compose ─────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield HealthDashboard(id="dashboard")
        yield LevelFilterBar(id="level-filter-bar")

        with Horizontal(id="health-top-bar"):
            yield Input(
                placeholder="🔍 Global Search: filter by text or field (e.g. 'user_id:101')",
                id="health-search-bar",
            )
            yield Button("Reset All", id="health-btn-reset", variant="error")

        yield Static("", id="health-filter-indicator")
        yield Static(
            "⏸  PAUSED — logs buffering in background. Press [bold]P[/bold] to resume.",
            id="pause-banner",
        )

        with Horizontal(id="health-lower-pane"):
            with Vertical(id="health-sidebar"):
                yield Static("🔴 [b]Recent Errors[/b] (Click to Trace)", id="health-sidebar-title")
                yield ListView(id="health-trace-list")
            with Vertical(id="health-main-view"):
                yield Static("📜 [b]Live Log Stream[/b]", id="live-logs-title")
                yield RichLog(highlight=True, markup=True, wrap=True, id="health-log-view")
        yield Footer()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.dashboard        = self.query_one(HealthDashboard)
        self.trace_list       = self.query_one("#health-trace-list", ListView)
        self.log_view         = self.query_one("#health-log-view", RichLog)
        self.search_bar       = self.query_one("#health-search-bar", Input)
        self.filter_indicator = self.query_one("#health-filter-indicator", Static)
        self.pause_banner     = self.query_one("#pause-banner", Static)
        self.filter_indicator.display = False
        self.pause_banner.display     = False
        self._re_render_logs()

    # ── Event handlers ───────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "health-search-bar":
            self.active_search_query = event.value.strip()

    def on_level_filter_bar_filter_changed(self, event: LevelFilterBar.FilterChanged) -> None:
        """Handles clicks on the LevelFilterBar buttons."""
        self.active_level_filter = event.level

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TraceItem):
            self.app.switch_to_tracer(event.item.trace_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "health-btn-reset":
            self.action_reset_filter()

    # ── Watchers ─────────────────────────────────────────────────────────────

    def watch_active_search_query(self, _: str) -> None:
        self._re_render_logs()
        self._update_filter_indicator()

    def watch_active_level_filter(self, _: str) -> None:
        self._re_render_logs()
        self._update_filter_indicator()

    def watch_is_paused(self, paused: bool) -> None:
        if hasattr(self, "pause_banner"):
            self.pause_banner.display = paused

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_focus_search(self) -> None:
        self.search_bar.focus()

    def action_reset_filter(self) -> None:
        self.active_search_query = ""
        self.active_level_filter = "ALL"
        self.search_bar.value    = ""
        self.query_one(LevelFilterBar).set_level("ALL")
        self.log_view.focus()

    def action_toggle_pause(self) -> None:
        self.is_paused       = not self.is_paused
        self.app.is_paused   = self.is_paused
        label = "⏸ PAUSED" if self.is_paused else "▶ RESUMED"
        self.notify(label, timeout=2)

    def action_set_level(self, level: str) -> None:
        """Called by number-key bindings."""
        self.active_level_filter = level
        self.query_one(LevelFilterBar).set_level(level)

    def action_export_logs(self) -> None:
        """Export currently filtered logs to a timestamped JSON file."""
        export_logs = [
            (e["dict"] if e["dict"] else {"raw": e["raw"]})
            for e in self.app.log_store.logs
            if self._log_passes_filters(e)
        ]

        if not export_logs:
            self.notify("⚠️  No logs match current filters — nothing to export.", severity="warning", timeout=3)
            return

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"superlog_export_{ts}.json"
        filepath = os.path.join(os.getcwd(), filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(export_logs, f, indent=2, default=str)
            self.notify(f"✅ {len(export_logs)} logs → {filename}", timeout=5)
        except OSError as e:
            self.notify(f"❌ Export failed: {e}", severity="error", timeout=5)

    def action_quit(self) -> None:
        self.app.exit()

    # ── Log processing ────────────────────────────────────────────────────────

    def _level_passes_filter(self, level: str) -> bool:
        if self.active_level_filter == "ALL":
            return True
        filter_rank = LEVEL_ORDER.get(self.active_level_filter, 0)
        entry_rank  = LEVEL_ORDER.get(level, -1)
        return entry_rank >= filter_rank

    def _log_passes_filters(self, log_entry: dict) -> bool:
        if not self._level_passes_filter(log_entry["level"]):
            return False
        log_dict = log_entry["dict"]
        q = self.active_search_query.lower()
        if q:
            if ":" in q:
                key, val = (p.strip() for p in q.split(":", 1))
                if not log_dict or str(log_dict.get(key, "")).lower() != val:
                    return False
            else:
                if q not in log_entry["raw"].lower():
                    return False
        return True

    def _re_render_logs(self) -> None:
        if not hasattr(self, "log_view"):
            return
        self.log_view.clear()
        # Snapshot to avoid RuntimeError if tail thread mutates the deque;
        # cap at last 500 so re-renders stay instant regardless of log volume.
        logs_snapshot = list(self.app.log_store.logs)[-500:]
        for entry in logs_snapshot:
            self._process_log_for_view(entry)

    def _process_log_for_view(self, log_entry: dict) -> None:
        if self._log_passes_filters(log_entry):
            self.log_view.write(log_entry["rich_text"])

    def process_log(self, log_entry: dict) -> None:
        """Called live from tail thread (only when not paused)."""
        if not self.is_paused and hasattr(self, "log_view"):
            self._process_log_for_view(log_entry)

    def add_trace(self, trace_id: str) -> None:
        self.trace_list.mount(TraceItem(trace_id), before=0)

    def _update_filter_indicator(self) -> None:
        if not hasattr(self, "filter_indicator"):
            return
        parts = []
        if self.active_level_filter != "ALL":
            parts.append(f"📊 [b]Level:[/b] {self.active_level_filter}+")
        if self.active_search_query:
            parts.append(f"🔍 [b]Search:[/b] '{self.active_search_query}'")
        if parts:
            self.filter_indicator.update("  |  ".join(parts) + "  [dim](Press 'r' to clear)[/dim]")
            self.filter_indicator.display = True
        else:
            self.filter_indicator.display = False


# ════════════════════════════════════════════════════════════════════════════
# TracerScreen  (deep-dive trace view)
# ════════════════════════════════════════════════════════════════════════════

class TracerScreen(Screen):
    """The deep dive tracing screen."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "go_back", "Back"),
        ("b", "go_back", "Back"),
        ("c", "clear", "Clear View"),
        ("r", "reset_filter", "Reset Filters"),
        ("f", "focus_search", "Search"),
        ("/", "focus_search", "Search"),
    ]

    active_trace_filter  = reactive(None)
    active_search_query  = reactive("")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="tracer-top-bar"):
            yield Button("← Back to Live Logs", id="btn-back", variant="primary")
            yield Input(
                placeholder="🔍 Insights: search by text or field (e.g. 'user_id:101')",
                id="search-bar",
            )
            yield Button("Reset Search", id="btn-reset", variant="error")

        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("🔴 [b]Error Traces[/b]", id="sidebar-title")
                yield ListView(id="trace-list")
            with Vertical(id="main-view"):
                yield Static("", id="filter-indicator")
                yield RichLog(highlight=True, markup=True, wrap=True, id="tracer-log-view")
        yield Footer()

    def on_mount(self) -> None:
        self.log_view         = self.query_one("#tracer-log-view", RichLog)
        self.trace_list       = self.query_one("#trace-list", ListView)
        self.filter_indicator = self.query_one("#filter-indicator", Static)
        self.search_bar       = self.query_one("#search-bar", Input)
        self.filter_indicator.display = False
        self._re_render_logs()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.action_go_back()
        elif event.button.id == "btn-reset":
            self.action_reset_filter()

    def action_quit(self)    -> None: self.app.exit()
    def action_go_back(self) -> None: self.app.pop_screen()
    def action_clear(self)   -> None: self.log_view.clear()

    def set_trace(self, trace_id: str) -> None:
        self.active_trace_filter = trace_id

    def add_trace(self, trace_id: str) -> None:
        self.trace_list.mount(TraceItem(trace_id), before=0)

    def watch_active_trace_filter(self, _) -> None:  self._re_render_logs()
    def watch_active_search_query(self, _) -> None:  self._re_render_logs()

    def _re_render_logs(self) -> None:
        if not hasattr(self, "log_view"):
            return
        self.log_view.clear()
        parts = []
        if self.active_trace_filter:
            parts.append(f"🎯 [b]Trace ID:[/b] {self.active_trace_filter}")
        if self.active_search_query:
            parts.append(f"🔍 [b]Query:[/b] '{self.active_search_query}'")
        if parts:
            self.filter_indicator.update(" | ".join(parts) + "  [dim](Press 'r' to reset)[/dim]")
            self.filter_indicator.display = True
        else:
            self.filter_indicator.display = False

        # Snapshot to avoid RuntimeError; cap at last 500 for performance.
        logs_snapshot = list(self.app.log_store.logs)[-500:]
        for entry in logs_snapshot:
            self._process_log_for_view(entry)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TraceItem):
            self.active_trace_filter = event.item.trace_id

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-bar":
            self.active_search_query = event.value.strip()

    def action_reset_filter(self) -> None:
        self.active_trace_filter = None
        self.active_search_query = ""
        self.search_bar.value    = ""
        self.trace_list.index    = None
        self.log_view.focus()

    def action_focus_search(self) -> None:
        self.search_bar.focus()

    def _process_log_for_view(self, log_entry: dict) -> None:
        log_dict = log_entry["dict"]
        if self.active_trace_filter:
            if not log_dict or log_dict.get("trace_id") != self.active_trace_filter:
                return
        if self.active_search_query:
            q = self.active_search_query.lower()
            if ":" in q:
                key, val = (p.strip() for p in q.split(":", 1))
                if not log_dict or str(log_dict.get(key, "")).lower() != val:
                    return
            else:
                if q not in log_entry["raw"].lower():
                    return
        self.log_view.write(log_entry["rich_text"])

    def process_log(self, log_entry: dict) -> None:
        if hasattr(self, "log_view"):
            self._process_log_for_view(log_entry)


# ════════════════════════════════════════════════════════════════════════════
# LogViewerApp
# ════════════════════════════════════════════════════════════════════════════

class LogViewerApp(App):
    """SuperLog TUI — real-time structured log viewer."""

    CSS_PATH  = "log_viewer.tcss"
    is_paused: bool = False  # shared flag read by the tail thread

    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.log_store = LogStore(max_size=10000)
        self.title     = f"🚀 SuperLog — {self.file_path}"

    def on_mount(self) -> None:
        self.install_screen(HealthScreen(), "health")
        self.install_screen(TracerScreen(), "tracer")
        self.push_screen("health")
        self.run_worker(self.tail_file, exclusive=True, thread=True)
        self.set_interval(1.0, self.update_dashboard)

    def switch_to_tracer(self, trace_id: str) -> None:
        self.push_screen("tracer")
        self.get_screen("tracer").set_trace(trace_id)

    def update_dashboard(self) -> None:
        self.log_store.tick_second()
        try:
            health = self.get_screen("health")
            if health.is_current or health.is_active:
                health.dashboard.update_metrics(self.log_store)

                # ── Threshold alerts ─────────────────────────────────────
                error_alert   = self.log_store.errors_per_sec[-1] >= ALERT_ERROR_THRESHOLD
                latency_alert = self.log_store.get_avg_latency() * 1000 > ALERT_LATENCY_MS
                health.dashboard.set_alert_state(error_alert, latency_alert)
        except Exception:
            pass

    def tail_file(self) -> None:
        worker            = get_current_worker()
        last_pos          = 0
        known_error_traces: set = set()

        while not worker.is_cancelled:
            if not os.path.exists(self.file_path):
                time.sleep(1)
                continue

            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    f.seek(last_pos)
                    while not worker.is_cancelled:
                        line = f.readline()
                        if not line:
                            break

                        log_dict          = parse_json_log(line)
                        rich_text, level  = format_log_line(log_dict, line)
                        self.log_store.add_log(line, log_dict, level, rich_text)

                        # Propagate new error traces to both screens
                        if len(self.log_store.error_traces_set) > len(known_error_traces):
                            for t in self.log_store.error_traces_set - known_error_traces:
                                known_error_traces.add(t)
                                self.call_from_thread(self._add_trace_to_screens, t)

                        # Only push to UI when not paused
                        if not self.is_paused:
                            log_entry = self.log_store.logs[-1]
                            self.call_from_thread(self._dispatch_log, log_entry)

                    last_pos = f.tell()
            except PermissionError:
                pass

            time.sleep(0.1)

    def _add_trace_to_screens(self, trace_id: str) -> None:
        try:
            self.get_screen("health").add_trace(trace_id)
            self.get_screen("tracer").add_trace(trace_id)
        except Exception:
            pass

    def _dispatch_log(self, log_entry: dict) -> None:
        try:
            self.get_screen("health").process_log(log_entry)
            self.get_screen("tracer").process_log(log_entry)
        except Exception:
            pass
