"""
Simple JSON-file history store for repeated-entry fields.
Saves to ~/.espflasher_history.json; max MAX_ITEMS per key.
"""

import json
import os

_PATH = os.path.join(os.path.expanduser("~"), ".espflasher_history.json")
MAX_ITEMS = 8


def load() -> dict:
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save(data: dict) -> None:
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def push(data: dict, key: str, value: str) -> dict:
    """Add value to the front of data[key], deduplicate, cap at MAX_ITEMS."""
    if not value or not value.strip():
        return data
    value = value.strip()
    items = [v for v in data.get(key, []) if v != value]
    items.insert(0, value)
    data[key] = items[:MAX_ITEMS]
    return data


def get_list(data: dict, key: str) -> list[str]:
    return data.get(key, [])
