"""
superlog.logger
~~~~~~~~~~~~~~~
Drop-in structured JSON logger for your Python application.

Usage:
    from superlog import get_logger, trace, set_trace_id

    logger = get_logger("myapp", log_file="app.log")

    @trace(log_args=True, log_result=True)
    def my_function(x: int) -> int:
        logger.info("Processing", extra={"value": x})
        return x * 2
"""
import logging
import json
import time
import uuid
import sys
import contextvars
import functools
from typing import Optional, Any, Callable, Dict
from datetime import datetime, timezone


# ── Context variable — carries trace_id across async tasks / threads ────────
_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)


def set_trace_id(trace_id: Optional[str] = None) -> str:
    """
    Attach a trace ID to the current execution context.
    Generates a UUID4 if none is provided.

    Returns the trace ID that was set.
    """
    if not trace_id:
        trace_id = str(uuid.uuid4())
    _trace_id_var.set(trace_id)
    return trace_id


def get_trace_id() -> Optional[str]:
    """Return the trace ID for the current context, or None if not set."""
    return _trace_id_var.get()


# ── JSON Formatter ──────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    _STANDARD_ATTRS = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "level":     record.levelname,
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "message":   record.getMessage(),
        }

        trace_id = get_trace_id()
        if trace_id:
            log_obj["trace_id"] = trace_id

        # Merge any extra={"key": value} fields
        for key, value in record.__dict__.items():
            if key not in self._STANDARD_ATTRS and key not in log_obj:
                try:
                    json.dumps(value)
                    log_obj[key] = value
                except (TypeError, OverflowError):
                    log_obj[key] = str(value)

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


# ── Logger factory ──────────────────────────────────────────────────────────

def get_logger(
    name: str,
    log_file: Optional[str] = None,
    level: int | str = logging.INFO,
) -> logging.Logger:
    """
    Create and configure a JSON-formatted logger.

    Args:
        name:     Logger name (usually your module/app name).
        log_file: Optional path to write logs to. Logs also go to stdout.
        level:    Logging level (e.g. logging.DEBUG or "DEBUG").

    Returns:
        A configured :class:`logging.Logger` instance.

    Example::

        logger = get_logger("myapp", log_file="app.log", level="DEBUG")
        logger.info("Ready", extra={"port": 8080})
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers if called multiple times
    if logger.hasHandlers():
        logger.handlers.clear()

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    logger.setLevel(level)
    formatter = JSONFormatter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


# ── @trace decorator ────────────────────────────────────────────────────────

def trace(
    log_args:    bool = False,
    log_result:  bool = False,
    logger_name: str  = "app",
):
    """
    Decorator — automatically logs entry, exit, and execution time of a function.

    Args:
        log_args:    Log the function's arguments on entry.
        log_result:  Log the function's return value on exit.
        logger_name: Name of the logger to use.

    Example::

        @trace(log_args=True, log_result=True)
        def fetch_user(user_id: int):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(logger_name)
            extra  = {"function": func.__name__}

            if log_args:
                extra["func_args"]   = [str(a) for a in args]
                extra["func_kwargs"] = {k: str(v) for k, v in kwargs.items()}

            logger.debug(f"Entering {func.__name__}", extra=extra)
            start = time.perf_counter()

            try:
                result          = func(*args, **kwargs)
                elapsed         = time.perf_counter() - start
                exit_extra: Dict[str, Any] = {
                    "function":           func.__name__,
                    "execution_time_sec": round(elapsed, 4),
                }
                if log_result:
                    exit_extra["result"] = str(result)
                logger.debug(f"Exiting {func.__name__}", extra=exit_extra)
                return result

            except Exception as e:
                elapsed = time.perf_counter() - start
                logger.error(
                    f"Exception in {func.__name__}: {e}",
                    exc_info=True,
                    extra={
                        "function":           func.__name__,
                        "execution_time_sec": round(elapsed, 4),
                    },
                )
                raise

        return wrapper
    return decorator
