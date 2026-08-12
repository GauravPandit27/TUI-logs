import json
from rich.text import Text

def parse_json_log(line: str) -> dict:
    """Parses a string line into a JSON dictionary, returns None if invalid."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None

def format_log_line(log_dict: dict, raw_line: str) -> tuple[Text, str]:
    """
    Takes a parsed JSON log dictionary and returns a colored Rich Text object,
    along with the level for stats processing.
    """
    if log_dict is None:
        # Not a valid JSON, just return raw string as basic text
        return Text(raw_line.strip(), style="dim"), "UNKNOWN"
        
    level = log_dict.get("level", "INFO").upper()
    timestamp = log_dict.get("timestamp", log_dict.get("time", ""))
    msg = log_dict.get("message", log_dict.get("msg", ""))
    
    # Extract any extra fields to display them nicely
    extra = {k: v for k, v in log_dict.items() if k not in ["level", "timestamp", "time", "message", "msg"]}
    
    color = "white"
    if level == "ERROR":
        color = "red bold"
    elif level in ["WARN", "WARNING"]:
        level = "WARN" # Standardize
        color = "yellow bold"
    elif level == "INFO":
        color = "cyan"
    elif level == "DEBUG":
        color = "magenta"
    
    text = Text()
    if timestamp:
        text.append(f"[{timestamp}] ", style="bright_black")
    text.append(f"{level:<7} ", style=color)
    text.append(f"{msg}")
    
    if extra:
        extra_str = f" {extra}"
        text.append(extra_str, style="dim italic")
        
    return text, level
