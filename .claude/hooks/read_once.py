#!/usr/bin/env python3
"""PreToolUse hook: block redundant Read calls to the same file+range."""

from __future__ import annotations

import json
import os
import sys
import time

STATE_FILE = "/tmp/.claude_read_once_state.json"
MAX_AGE_SECONDS = 6 * 3600


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"seen": {}}
    try:
        age = time.time() - os.path.getmtime(STATE_FILE)
        if age > MAX_AGE_SECONDS:
            return {"seen": {}}
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"seen": {}}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def main() -> None:
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    offset = tool_input.get("offset", 0)
    limit = tool_input.get("limit", 0)

    key = f"{file_path}:{offset}:{limit}"

    state = _load_state()
    seen: dict[str, int] = state.get("seen", {})

    if key in seen:
        json.dump(
            {
                "decision": "block",
                "reason": f"Already read: {file_path} (offset={offset}, limit={limit})",
            },
            sys.stdout,
        )
    else:
        seen[key] = int(time.time())
        state["seen"] = seen
        _save_state(state)
        json.dump({"decision": "approve"}, sys.stdout)


if __name__ == "__main__":
    main()
