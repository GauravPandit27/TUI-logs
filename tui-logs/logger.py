import logging
import json
import time
import uuid
import sys
import contextvars
from typing import Optional, Any, Callable, Dict
import functools
from datetime import datetime, timezone

# Context variable to hold the trace_id for the current async task/thread
_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)

def set_trace_id(trace_id: Optional[str] = None) -> str:
    """Sets a trace ID for the current context. Generates a UUID if none is provided."""
    if not trace_id:
        trace_id = str(uuid.uuid4())
    _trace_id_var.set(trace_id)
    return trace_id

def get_trace_id() -> Optional[str]:
    """Gets the current trace ID from the context."""
    return _trace_id_var.get()

class JSONFormatter(logging.Formatter):
    """Custom formatter to output logs as JSON."""
    
    def format(self, record: logging.LogRecord) -> str:
        # Standard fields expected by the TUI
        log_obj: Dict[str, Any] = {
            "level": record.levelname,
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "message": record.getMessage()
        }

        # Include trace ID if available
        trace_id = get_trace_id()
        if trace_id:
            log_obj["trace_id"] = trace_id

        # Include any extra attributes added to the LogRecord (via extra={})
        # We filter out the standard LogRecord attributes
        standard_attrs = {
            'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename', 
            'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName', 
            'created', 'msecs', 'relativeCreated', 'thread', 'threadName', 'processName', 
            'process', 'taskName'
        }
        
        for key, value in record.__dict__.items():
            if key not in standard_attrs and key not in log_obj:
                # Basic serialization for extra fields, fallback to string
                try:
                    json.dumps(value) # test if serializable
                    log_obj[key] = value
                except (TypeError, OverflowError):
                    log_obj[key] = str(value)

        # Include exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_obj)

def get_logger(name: str, log_file: Optional[str] = None, level: int = logging.INFO) -> logging.Logger:
    """
    Creates and configures a JSON logger.
    If log_file is provided, logs will be written to that file.
    Always logs to stdout as well.
    """
    logger = logging.getLogger(name)
    
    # Avoid adding handlers multiple times if get_logger is called repeatedly
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.setLevel(level)
    
    formatter = JSONFormatter()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
    return logger

def trace(log_args: bool = False, log_result: bool = False, logger_name: str = "app"):
    """
    A decorator that logs the entry, exit, and execution time of a function.
    
    :param log_args: If True, logs the arguments passed to the function.
    :param log_result: If True, logs the return value of the function.
    :param logger_name: The name of the logger to use.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(logger_name)
            
            extra_start = {"function": func.__name__}
            if log_args:
                # Convert args/kwargs to strings to avoid serialization issues
                extra_start["func_args"] = [str(a) for a in args]
                extra_start["func_kwargs"] = {k: str(v) for k, v in kwargs.items()}
                
            logger.debug(f"Entering {func.__name__}", extra=extra_start)
            
            start_time = time.perf_counter()
            
            try:
                result = func(*args, **kwargs)
                execution_time = time.perf_counter() - start_time
                
                extra_end = {
                    "function": func.__name__,
                    "execution_time_sec": round(execution_time, 4)
                }
                if log_result:
                    extra_end["result"] = str(result)
                    
                logger.debug(f"Exiting {func.__name__}", extra=extra_end)
                return result
                
            except Exception as e:
                execution_time = time.perf_counter() - start_time
                logger.error(
                    f"Exception in {func.__name__}: {str(e)}", 
                    exc_info=True,
                    extra={"function": func.__name__, "execution_time_sec": round(execution_time, 4)}
                )
                raise
                
        return wrapper
    return decorator
