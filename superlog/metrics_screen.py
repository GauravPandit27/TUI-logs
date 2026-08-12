"""
superlog.metrics_screen
~~~~~~~~~~~~~~~~~~~~~~~
Live system metrics screen — CPU, memory, disk, network, top processes.
Powered by psutil. Press 'm' from the main dashboard to open.
"""
import time
from collections import deque

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Label, Sparkline, DataTable, Button
from textual.screen import Screen


# ── Rendering helpers ────────────────────────────────────────────────────────

def _bar(pct: float, width: int = 24) -> str:
    """Return a coloured Unicode block progress bar as Rich markup."""
    pct = max(0.0, min(100.0, pct))
    filled = int(width * pct / 100)
    bar    = "█" * filled + "░" * (width - filled)
    if pct > 85:
        color = "#f38ba8"   # red
    elif pct > 60:
        color = "#f9e2af"   # amber
    else:
        color = "#a6e3a1"   # green
    return f"[{color}]{bar}[/{color}] [bold]{pct:.1f}%[/bold]"


def _fmt(n: float, suffix: str = "B") -> str:
    """Human-readable byte size (or any unit)."""
    for unit in ("", "K", "M", "G", "T"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}{suffix}"
        n /= 1024
    return f"{n:.1f} P{suffix}"


# ════════════════════════════════════════════════════════════════════════════
# SystemMetricsScreen
# ════════════════════════════════════════════════════════════════════════════

class SystemMetricsScreen(Screen):
    """
    Full-screen system health dashboard.
    Refreshes every second via set_interval.
    """

    BINDINGS = [
        ("escape", "go_back", "Back to Logs"),
        ("b",      "go_back", "Back to Logs"),
        ("q",      "quit",    "Quit"),
    ]

    # Rolling 60-sample history for sparklines
    _cpu_history:    deque[float] = deque([0.0] * 60, maxlen=60)
    _net_sent_hist:  deque[float] = deque([0.0] * 60, maxlen=60)
    _net_recv_hist:  deque[float] = deque([0.0] * 60, maxlen=60)

    # Network rate tracking
    _net_prev:      tuple[int, int] = (0, 0)
    _net_prev_time: float           = 0.0

    # ── Compose ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # Top bar
        with Horizontal(id="metrics-topbar"):
            yield Button("← Back to Logs", id="metrics-btn-back", classes="metrics-back-btn")
            yield Static(
                "🖥️  [bold]System Metrics[/bold]  [dim]— live, 1s refresh[/dim]",
                id="metrics-topbar-title",
            )

        if not _PSUTIL_OK:
            yield Static(
                "[red bold]psutil not installed.[/red bold]\n"
                "Run:  [yellow]pip install psutil[/yellow]",
                id="metrics-no-psutil",
            )
            yield Footer()
            return

        # ── Four stat panels ─────────────────────────────────────────────
        with Horizontal(id="metrics-panels"):

            # CPU
            with Vertical(id="panel-cpu", classes="stat-panel"):
                yield Label("🔲  CPU", classes="stat-panel-title")
                yield Label("", id="cpu-overall",  classes="stat-value")
                yield Sparkline(data=list(self._cpu_history), summary_function=max, id="cpu-sparkline")
                yield Static("", id="cpu-cores")

            # Memory
            with Vertical(id="panel-mem", classes="stat-panel"):
                yield Label("💾  Memory", classes="stat-panel-title")
                yield Label("", id="mem-label",   classes="stat-value")
                yield Label("", id="mem-bar")
                yield Label("", id="swap-label",  classes="stat-value")
                yield Label("", id="swap-bar")

            # Disk
            with Vertical(id="panel-disk", classes="stat-panel"):
                yield Label("💿  Disk", classes="stat-panel-title")
                yield Static("", id="disk-info")

            # Network
            with Vertical(id="panel-net", classes="stat-panel"):
                yield Label("🌐  Network", classes="stat-panel-title")
                yield Label("", id="net-up",     classes="stat-value")
                yield Sparkline(data=list(self._net_sent_hist), summary_function=max, id="net-up-spark")
                yield Label("", id="net-down",   classes="stat-value")
                yield Sparkline(data=list(self._net_recv_hist), summary_function=max, id="net-down-spark")
                yield Label("", id="net-totals", classes="stat-dim")

        # ── Process table ─────────────────────────────────────────────────
        with Vertical(id="panel-procs", classes="stat-panel"):
            yield Label("⚙️   Top Processes  [dim](sorted by CPU)[/dim]", classes="stat-panel-title")
            yield DataTable(id="proc-table", cursor_type="row")

        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        if not _PSUTIL_OK:
            return

        # Initialise DataTable columns
        tbl = self.query_one("#proc-table", DataTable)
        tbl.add_columns("PID", "Name", "CPU %", "Memory", "Threads", "Status")

        # Warm up psutil CPU measurements (first call always returns 0)
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

        # Baseline for network rate calculation
        net = psutil.net_io_counters()
        self._net_prev      = (net.bytes_sent, net.bytes_recv)
        self._net_prev_time = time.monotonic()

        # Kick off live refresh
        self.set_interval(1.0, self._tick)
        self._tick()

    # ── Main update tick ──────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._update_cpu()
        self._update_memory()
        self._update_disk()
        self._update_network()
        self._update_processes()

    # ── CPU ───────────────────────────────────────────────────────────────────

    def _update_cpu(self) -> None:
        overall   = psutil.cpu_percent(interval=None)
        per_core  = psutil.cpu_percent(interval=None, percpu=True)
        freq      = psutil.cpu_freq()

        self._cpu_history.append(overall)
        self.query_one("#cpu-sparkline", Sparkline).data = list(self._cpu_history)

        freq_str = f"  [dim]{freq.current:.0f} MHz[/dim]" if freq else ""
        self.query_one("#cpu-overall", Label).update(
            f"Overall{freq_str}\n{_bar(overall)}"
        )

        core_lines = []
        for i, pct in enumerate(per_core):
            filled = int(12 * pct / 100)
            mini   = "█" * filled + "░" * (12 - filled)
            color  = "#f38ba8" if pct > 85 else ("#f9e2af" if pct > 60 else "#a6e3a1")
            core_lines.append(f"Core {i:<2}  [{color}]{mini}[/{color}]  {pct:5.1f}%")

        self.query_one("#cpu-cores", Static).update("\n".join(core_lines))

    # ── Memory ───────────────────────────────────────────────────────────────

    def _update_memory(self) -> None:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()

        used_gb  = vm.used  / 1024 ** 3
        total_gb = vm.total / 1024 ** 3
        avail_gb = vm.available / 1024 ** 3

        self.query_one("#mem-label", Label).update(
            f"RAM  {used_gb:.1f} / {total_gb:.1f} GB  "
            f"[dim](free {avail_gb:.1f} GB)[/dim]"
        )
        self.query_one("#mem-bar", Label).update(_bar(vm.percent))

        su_gb = sw.used  / 1024 ** 3
        st_gb = sw.total / 1024 ** 3
        self.query_one("#swap-label", Label).update(
            f"Swap {su_gb:.1f} / {st_gb:.1f} GB"
        )
        self.query_one("#swap-bar", Label).update(_bar(sw.percent))

    # ── Disk ─────────────────────────────────────────────────────────────────

    def _update_disk(self) -> None:
        lines: list[str] = []
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            used_gb  = u.used  / 1024 ** 3
            total_gb = u.total / 1024 ** 3
            mp = part.mountpoint if len(part.mountpoint) <= 20 else part.mountpoint[:18] + "…"
            lines += [
                f"[bold]{mp}[/bold]",
                f"  {used_gb:.1f} / {total_gb:.1f} GB",
                f"  {_bar(u.percent, width=20)}",
                "",
            ]
        self.query_one("#disk-info", Static).update("\n".join(lines).rstrip())

    # ── Network ───────────────────────────────────────────────────────────────

    def _update_network(self) -> None:
        now    = time.monotonic()
        net    = psutil.net_io_counters()
        dt     = max(now - self._net_prev_time, 0.001)

        sent_s = (net.bytes_sent - self._net_prev[0]) / dt
        recv_s = (net.bytes_recv - self._net_prev[1]) / dt

        self._net_prev      = (net.bytes_sent, net.bytes_recv)
        self._net_prev_time = now

        self._net_sent_hist.append(sent_s)
        self._net_recv_hist.append(recv_s)

        up_col   = "#f38ba8" if sent_s > 5 * 1024 ** 2 else "#cdd6f4"
        down_col = "#f38ba8" if recv_s > 10 * 1024 ** 2 else "#a6e3a1"

        self.query_one("#net-up",   Label).update(
            f"↑ Upload    [{up_col}]{_fmt(sent_s)}/s[/{up_col}]"
        )
        self.query_one("#net-up-spark", Sparkline).data = list(self._net_sent_hist)

        self.query_one("#net-down", Label).update(
            f"↓ Download  [{down_col}]{_fmt(recv_s)}/s[/{down_col}]"
        )
        self.query_one("#net-down-spark", Sparkline).data = list(self._net_recv_hist)

        self.query_one("#net-totals", Label).update(
            f"[dim]Sent {_fmt(net.bytes_sent)}  •  Recv {_fmt(net.bytes_recv)}[/dim]"
        )

    # ── Processes ─────────────────────────────────────────────────────────────

    def _update_processes(self) -> None:
        tbl = self.query_one("#proc-table", DataTable)
        tbl.clear()

        procs: list[dict] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "num_threads", "status"]):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        procs.sort(key=lambda x: x.get("cpu_percent") or 0.0, reverse=True)

        for info in procs[:18]:
            cpu     = info.get("cpu_percent") or 0.0
            mem_inf = info.get("memory_info")
            mem_str = _fmt(mem_inf.rss) if mem_inf else "—"
            name    = (info.get("name") or "?")[:28]
            status  = info.get("status", "?")
            threads = str(info.get("num_threads") or "?")

            cpu_col = "#f38ba8" if cpu > 50 else ("#f9e2af" if cpu > 20 else "#cdd6f4")
            tbl.add_row(
                str(info.get("pid", "?")),
                name,
                f"[{cpu_col}]{cpu:6.1f}%[/{cpu_col}]",
                mem_str,
                threads,
                status,
            )

    # ── Actions ───────────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "metrics-btn-back":
            self.action_go_back()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()
