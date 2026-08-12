import json
# pyrefly: ignore [missing-import]
from rich.text import Text


def parse_json_log(line: str) -> dict | None:
    """Parse a string line into a JSON dict; returns None if not valid JSON."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def format_log_line(log_dict: dict | None, raw_line: str) -> tuple[Text, str]:
    """
    Convert a parsed JSON log dict into a coloured Rich Text object.

    Returns:
        (rich_text, level_string)
    """
    if log_dict is None:
        return Text(raw_line.strip(), style="dim"), "UNKNOWN"

    level     = log_dict.get("level", "INFO").upper()
    timestamp = log_dict.get("timestamp", log_dict.get("time", ""))
    msg       = log_dict.get("message",   log_dict.get("msg", ""))

    # Extra fields — everything except the standard keys
    extra = {
        k: v for k, v in log_dict.items()
        if k not in {"level", "timestamp", "time", "message", "msg"}
    }

    if level in ("WARN", "WARNING"):
        level = "WARN"
        color = "yellow bold"
    elif level == "ERROR":
        color = "red bold"
    elif level == "INFO":
        color = "cyan"
    elif level == "DEBUG":
        color = "magenta"
    else:
        color = "white"

    text = Text()
    if timestamp:
        text.append(f"[{timestamp}] ", style="bright_black")
    text.append(f"{level:<7} ", style=color)
    text.append(msg)
    if extra:
        text.append(f" {extra}", style="dim italic")

    return text, level
