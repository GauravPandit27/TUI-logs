"""
superlog.app
~~~~~~~~~~~~
Main Textual application — log viewer with dashboard, tracer, and metrics.
"""
import json
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Header, Footer, RichLog, Static, ListView, ListItem,
    Label, Sparkline, Input, Button,
)
from textual.reactive import reactive
from textual.screen import Screen
from textual.message import Message
# pyrefly: ignore [missing-import]
from textual.worker import get_current_worker

from .highlighter import parse_json_log, format_log_line
from .metrics_screen import SystemMetricsScreen

# ── Alert thresholds ─────────────────────────────────────────────────────────
ALERT_ERROR_THRESHOLD = 3    # errors/sec → red border
ALERT_LATENCY_MS      = 500  # avg ms    → amber border

# ── Level filter helpers ──────────────────────────────────────────────────────
LEVEL_ORDER  = {"DEBUG": 0, "INFO": 1, "WARN": 2, "WARNING": 2, "ERROR": 3, "UNKNOWN": -1}
LEVEL_LABELS = ["ALL", "DEBUG", "INFO", "WARN", "ERROR"]


# ════════════════════════════════════════════════════════════════════════════
# LogStore
# ════════════════════════════════════════════════════════════════════════════

class LogStore:
    def __init__(self, max_size: int = 10_000):
        self.start_time       = time.time()
        self.max_size         = max_size
        self.logs             = deque(maxlen=max_size)
        self.error_traces     = []
        self.error_traces_set: set = set()

        self.total_logs         = 0
        self.total_errors       = 0
        self.logs_per_sec       = deque([0] * 60, maxlen=60)
        self.errors_per_sec     = deque([0] * 60, maxlen=60)
        self._current_sec_logs   = 0
        self._current_sec_errors = 0

        self.recent_latencies = deque(maxlen=100)

    def add_log(self, raw_line: str, log_dict: dict, level: str, rich_text) -> None:
        self.logs.append({"raw": raw_line, "dict": log_dict, "level": level, "rich_text": rich_text})
        self.total_logs        += 1
        self._current_sec_logs += 1

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
        d = int(time.time() - self.start_time)
        m, s = divmod(d, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


# ════════════════════════════════════════════════════════════════════════════
# HealthDashboard
# ════════════════════════════════════════════════════════════════════════════

class MetricPanel(Vertical):
    def __init__(self, title: str, color: str, id: str):
        super().__init__(id=id)
        self.panel_title = title
        self.color       = color

    def compose(self) -> ComposeResult:
        yield Label(f"[b]{self.panel_title}[/b]", classes="metric-title")
        yield Label("0", classes="metric-value", id=f"{self.id}-val")
        yield Sparkline(data=[0] * 60, summary_function=max, id=f"{self.id}-spark")

    def on_mount(self) -> None:
        self.query_one(f"#{self.id}-spark", Sparkline).styles.tint = self.color


class HealthDashboard(Horizontal):
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
        ec = "#f38ba8" if store.errors_per_sec[-1] > 0 else "#cdd6f4"
        self.query_one("#metric-errors-val", Label).update(
            f"[{ec}]{store.errors_per_sec[-1]}[/{ec}]  [dim](Total: {store.total_errors})[/dim]"
        )
        lat   = store.get_avg_latency() * 1000
        lc    = "#f38ba8" if lat > 500 else ("#f9e2af" if lat > 200 else "#a6e3a1")
        self.query_one("#latency-val", Label).update(f"[{lc}]{lat:.2f}ms[/{lc}]")
        self.query_one("#uptime-val",  Label).update(f"[b]{store.get_uptime_str()}[/b]")
        self.query_one("#metric-volume-spark", Sparkline).data = list(store.logs_per_sec)
        self.query_one("#metric-errors-spark", Sparkline).data = list(store.errors_per_sec)

    def set_alert_state(self, error: bool, latency: bool) -> None:
        if error:
            self.add_class("alert-error");   self.remove_class("alert-latency")
        elif latency:
            self.add_class("alert-latency"); self.remove_class("alert-error")
        else:
            self.remove_class("alert-error"); self.remove_class("alert-latency")


# ════════════════════════════════════════════════════════════════════════════
# LevelFilterBar
# ════════════════════════════════════════════════════════════════════════════

class LevelFilterBar(Horizontal):
    class FilterChanged(Message):
        def __init__(self, level: str) -> None:
            super().__init__()
            self.level = level

    def compose(self) -> ComposeResult:
        yield Static("FILTER:", classes="filter-bar-label")
        for lvl in LEVEL_LABELS:
            extra = " level-btn-active" if lvl == "ALL" else ""
            yield Button(lvl, id=f"lvl-btn-{lvl.lower()}",
                         classes=f"level-btn level-btn-{lvl.lower()}{extra}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id or ""
        if bid.startswith("lvl-btn-"):
            lvl = bid.replace("lvl-btn-", "").upper()
            self._set_active(lvl)
            self.post_message(self.FilterChanged(lvl))

    def set_level(self, level: str) -> None:
        self._set_active(level)

    def _set_active(self, active: str) -> None:
        for btn in self.query(".level-btn"):
            btn.remove_class("level-btn-active")
        try:
            self.query_one(f"#lvl-btn-{active.lower()}", Button).add_class("level-btn-active")
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# Shared
# ════════════════════════════════════════════════════════════════════════════

class TraceItem(ListItem):
    def __init__(self, trace_id: str):
        super().__init__()
        self.trace_id = trace_id

    def compose(self) -> ComposeResult:
        yield Label(f"🎯 {self.trace_id[:8]}...", classes="trace-label")


# ════════════════════════════════════════════════════════════════════════════
# HealthScreen
# ════════════════════════════════════════════════════════════════════════════

class HealthScreen(Screen):
    BINDINGS = [
        ("q",   "quit",               "Quit"),
        ("f",   "focus_search",       "Search"),
        ("/",   "focus_search",       "Search"),
        ("r",   "reset_filter",       "Reset"),
        ("p",   "toggle_pause",       "Pause"),
        ("x",   "export_logs",        "Export"),
        ("m",   "open_metrics",       "Metrics"),
        ("1",   "set_level('ALL')",   "All"),
        ("2",   "set_level('DEBUG')", "Debug+"),
        ("3",   "set_level('INFO')",  "Info+"),
        ("4",   "set_level('WARN')",  "Warn+"),
        ("5",   "set_level('ERROR')", "Error"),
    ]

    active_search_query = reactive("")
    active_level_filter = reactive("ALL")
    is_paused           = reactive(False)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield HealthDashboard(id="dashboard")
        yield LevelFilterBar(id="level-filter-bar")

        with Horizontal(id="health-top-bar"):
            yield Input(
                placeholder="🔍 Search: text or field (e.g. 'user_id:101')  •  m=Metrics  p=Pause  x=Export",
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
                yield Static("🔴 [b]Recent Errors[/b]", id="health-sidebar-title")
                yield ListView(id="health-trace-list")
            with Vertical(id="health-main-view"):
                yield Static("📜 [b]Live Log Stream[/b]", id="live-logs-title")
                yield RichLog(highlight=True, markup=True, wrap=True, id="health-log-view")
        yield Footer()

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

    # ── Events ───────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "health-search-bar":
            self.active_search_query = event.value.strip()

    def on_level_filter_bar_filter_changed(self, event: LevelFilterBar.FilterChanged) -> None:
        self.active_level_filter = event.level

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TraceItem):
            self.app.switch_to_tracer(event.item.trace_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "health-btn-reset":
            self.action_reset_filter()

    # ── Watchers ──────────────────────────────────────────────────────────────

    def watch_active_search_query(self, _: str) -> None:
        self._re_render_logs(); self._update_indicator()

    def watch_active_level_filter(self, _: str) -> None:
        self._re_render_logs(); self._update_indicator()

    def watch_is_paused(self, paused: bool) -> None:
        if hasattr(self, "pause_banner"):
            self.pause_banner.display = paused

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_focus_search(self)  -> None: self.search_bar.focus()
    def action_open_metrics(self)  -> None: self.app.switch_to_metrics()
    def action_quit(self)          -> None: self.app.exit()

    def action_set_level(self, level: str) -> None:
        self.active_level_filter = level
        self.query_one(LevelFilterBar).set_level(level)

    def action_reset_filter(self) -> None:
        self.active_search_query = ""
        self.active_level_filter = "ALL"
        self.search_bar.value    = ""
        self.query_one(LevelFilterBar).set_level("ALL")
        self.log_view.focus()

    def action_toggle_pause(self) -> None:
        self.is_paused     = not self.is_paused
        self.app.is_paused = self.is_paused
        self.notify("⏸ PAUSED" if self.is_paused else "▶ RESUMED", timeout=2)

    def action_export_logs(self) -> None:
        rows = [
            (e["dict"] if e["dict"] else {"raw": e["raw"]})
            for e in self.app.log_store.logs
            if self._passes(e)
        ]
        if not rows:
            self.notify("⚠️  No logs match current filters.", severity="warning", timeout=3)
            return
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(os.getcwd(), f"superlog_export_{ts}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, default=str)
            self.notify(f"✅ {len(rows)} logs → superlog_export_{ts}.json", timeout=5)
        except OSError as e:
            self.notify(f"❌ Export failed: {e}", severity="error", timeout=5)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _level_ok(self, level: str) -> bool:
        if self.active_level_filter == "ALL":
            return True
        return LEVEL_ORDER.get(level, -1) >= LEVEL_ORDER.get(self.active_level_filter, 0)

    def _passes(self, entry: dict) -> bool:
        if not self._level_ok(entry["level"]):
            return False
        q = self.active_search_query.lower()
        if q:
            d = entry["dict"]
            if ":" in q:
                k, v = (p.strip() for p in q.split(":", 1))
                if not d or str(d.get(k, "")).lower() != v:
                    return False
            elif q not in entry["raw"].lower():
                return False
        return True

    def _re_render_logs(self) -> None:
        if not hasattr(self, "log_view"):
            return
        self.log_view.clear()
        for e in list(self.app.log_store.logs)[-500:]:
            if self._passes(e):
                self.log_view.write(e["rich_text"])

    def process_log(self, entry: dict) -> None:
        if not self.is_paused and hasattr(self, "log_view") and self._passes(entry):
            self.log_view.write(entry["rich_text"])

    def add_trace(self, trace_id: str) -> None:
        self.trace_list.mount(TraceItem(trace_id), before=0)

    def _update_indicator(self) -> None:
        if not hasattr(self, "filter_indicator"):
            return
        parts = []
        if self.active_level_filter != "ALL":
            parts.append(f"📊 [b]Level:[/b] {self.active_level_filter}+")
        if self.active_search_query:
            parts.append(f"🔍 [b]Search:[/b] '{self.active_search_query}'")
        if parts:
            self.filter_indicator.update("  |  ".join(parts) + "  [dim](r to clear)[/dim]")
            self.filter_indicator.display = True
        else:
            self.filter_indicator.display = False


# ════════════════════════════════════════════════════════════════════════════
# TracerScreen
# ════════════════════════════════════════════════════════════════════════════

class TracerScreen(Screen):
    BINDINGS = [
        ("q",      "quit",         "Quit"),
        ("escape", "go_back",      "Back"),
        ("b",      "go_back",      "Back"),
        ("c",      "clear",        "Clear"),
        ("r",      "reset_filter", "Reset"),
        ("f",      "focus_search", "Search"),
        ("/",      "focus_search", "Search"),
    ]

    active_trace_filter = reactive(None)
    active_search_query = reactive("")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="tracer-top-bar"):
            yield Button("← Back to Live Logs", id="btn-back", variant="primary")
            yield Input(placeholder="🔍 Search by text or field (e.g. 'user_id:101')", id="search-bar")
            yield Button("Reset", id="btn-reset", variant="error")
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
        {"btn-back": self.action_go_back, "btn-reset": self.action_reset_filter}.get(
            event.button.id or "", lambda: None
        )()

    def action_quit(self)          -> None: self.app.exit()
    def action_go_back(self)       -> None: self.app.pop_screen()
    def action_clear(self)         -> None: self.log_view.clear()
    def action_focus_search(self)  -> None: self.search_bar.focus()

    def set_trace(self, trace_id: str) -> None:
        self.active_trace_filter = trace_id

    def add_trace(self, trace_id: str) -> None:
        self.trace_list.mount(TraceItem(trace_id), before=0)

    def watch_active_trace_filter(self, _) -> None:  self._re_render_logs()
    def watch_active_search_query(self, _) -> None:  self._re_render_logs()

    def action_reset_filter(self) -> None:
        self.active_trace_filter = None
        self.active_search_query = ""
        self.search_bar.value    = ""
        self.trace_list.index    = None
        self.log_view.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TraceItem):
            self.active_trace_filter = event.item.trace_id

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-bar":
            self.active_search_query = event.value.strip()

    def _re_render_logs(self) -> None:
        if not hasattr(self, "log_view"):
            return
        self.log_view.clear()
        parts = []
        if self.active_trace_filter:
            parts.append(f"🎯 [b]Trace:[/b] {self.active_trace_filter}")
        if self.active_search_query:
            parts.append(f"🔍 [b]Query:[/b] '{self.active_search_query}'")
        if parts:
            self.filter_indicator.update(" | ".join(parts) + "  [dim](r to reset)[/dim]")
            self.filter_indicator.display = True
        else:
            self.filter_indicator.display = False

        for e in list(self.app.log_store.logs)[-500:]:
            self._write_if_match(e)

    def _write_if_match(self, entry: dict) -> None:
        d = entry["dict"]
        if self.active_trace_filter:
            if not d or d.get("trace_id") != self.active_trace_filter:
                return
        q = self.active_search_query.lower()
        if q:
            if ":" in q:
                k, v = (p.strip() for p in q.split(":", 1))
                if not d or str(d.get(k, "")).lower() != v:
                    return
            elif q not in entry["raw"].lower():
                return
        self.log_view.write(entry["rich_text"])

    def process_log(self, entry: dict) -> None:
        if hasattr(self, "log_view"):
            self._write_if_match(entry)


# ════════════════════════════════════════════════════════════════════════════
# LogViewerApp
# ════════════════════════════════════════════════════════════════════════════

class LogViewerApp(App):
    """SuperLog TUI — real-time structured log viewer."""

    CSS_PATH  = str(Path(__file__).parent / "log_viewer.tcss")
    is_paused: bool = False

    def __init__(self, file_path: str, max_lines: int = 10_000):
        super().__init__()
        self.file_path = file_path
        self.log_store = LogStore(max_size=max_lines)
        self.title     = f"🚀 SuperLog — {file_path}"

    def on_mount(self) -> None:
        self.install_screen(HealthScreen(),        "health")
        self.install_screen(TracerScreen(),        "tracer")
        self.install_screen(SystemMetricsScreen(), "metrics")
        self.push_screen("health")
        self.run_worker(self.tail_file, exclusive=True, thread=True)
        self.set_interval(1.0, self.update_dashboard)

    def switch_to_tracer(self, trace_id: str) -> None:
        self.push_screen("tracer")
        self.get_screen("tracer").set_trace(trace_id)

    def switch_to_metrics(self) -> None:
        self.push_screen("metrics")

    def update_dashboard(self) -> None:
        self.log_store.tick_second()
        try:
            h = self.get_screen("health")
            if h.is_current or h.is_active:
                h.dashboard.update_metrics(self.log_store)
                ea = self.log_store.errors_per_sec[-1] >= ALERT_ERROR_THRESHOLD
                la = self.log_store.get_avg_latency() * 1000 > ALERT_LATENCY_MS
                h.dashboard.set_alert_state(ea, la)
        except Exception:
            pass

    def tail_file(self) -> None:
        worker             = get_current_worker()
        last_pos           = 0
        known_traces: set  = set()

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

                        if len(self.log_store.error_traces_set) > len(known_traces):
                            for t in self.log_store.error_traces_set - known_traces:
                                known_traces.add(t)
                                self.call_from_thread(self._add_trace, t)

                        if not self.is_paused:
                            entry = self.log_store.logs[-1]
                            self.call_from_thread(self._dispatch, entry)

                    last_pos = f.tell()
            except PermissionError:
                pass
            time.sleep(0.1)

    def _add_trace(self, trace_id: str) -> None:
        for name in ("health", "tracer"):
            try:
                self.get_screen(name).add_trace(trace_id)
            except Exception:
                pass

    def _dispatch(self, entry: dict) -> None:
        for name in ("health", "tracer"):
            try:
                self.get_screen(name).process_log(entry)
            except Exception:
                pass
