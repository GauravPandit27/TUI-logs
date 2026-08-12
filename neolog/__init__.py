"""
NeoLog — Real-time structured log viewer and system monitor TUI.

Public API (use in your own projects):

    from neolog import get_logger, trace, set_trace_id

Example:
    logger = get_logger("myapp", log_file="app.log")
    logger.info("Server started", extra={"port": 8080})

    @trace(log_args=True)
    def fetch_data(user_id: int):
        ...
"""
from .logger import get_logger, trace, set_trace_id, get_trace_id

__version__ = "0.1.0"
__author__  = "Gaurav Pandit"
__all__     = ["get_logger", "trace", "set_trace_id", "get_trace_id"]
