#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-

import json
import sys
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class HookInput:
    """Represents the JSON input received by PostToolUse hooks."""

    session_id: str
    working_directory: str
    tool_name: str
    tool_input: Dict[str, Any]
    tool_response: Any


@dataclass
class HookOutput:
    """Represents the JSON output returned by hooks."""

    tool_response: Any
    decision: str = "approve"
    continue_: Optional[bool] = None
    stop_reason: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization, handling continue field name."""
        result = {"tool_response": self.tool_response, "decision": self.decision}

        if self.continue_ is not None:
            result["continue"] = self.continue_
        if self.stop_reason is not None:
            result["stopReason"] = self.stop_reason
        if self.reason is not None:
            result["reason"] = self.reason

        return result


def sanitize(text: str) -> str:
    """Redact likely sensitive values from text output."""
    username = (
        os.getenv("USER")
        or os.getenv("USERNAME")
        or os.path.basename(os.path.expanduser("~"))
    )
    patterns = [
        username,
        os.path.expanduser("~"),
        re.escape(os.path.expanduser("~")),
        r"/Users/[^/]+",  # Mac home directory path
        r"/home/[^/]+",  # Linux home directory path
        r"C:\\Users\\[^\\]+",  # Windows home directory path
    ]
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
    return text


def sanitize_tool_response(response: Any) -> Any:
    """Recursively sanitize tool response data."""
    if isinstance(response, str):
        return sanitize(response)
    elif isinstance(response, dict):
        return {key: sanitize_tool_response(value) for key, value in response.items()}
    elif isinstance(response, list):
        return [sanitize_tool_response(item) for item in response]
    else:
        return response


if __name__ == "__main__":
    try:
        # Read and parse JSON input from stdin
        raw_input = sys.stdin.read()
        input_dict = json.loads(raw_input)

        # Convert to dataclass
        hook_input = HookInput(
            session_id=input_dict.get("session_id", ""),
            working_directory=input_dict.get("working_directory", ""),
            tool_name=input_dict.get("tool_name", ""),
            tool_input=input_dict.get("tool_input", {}),
            tool_response=input_dict.get("tool_response", {}),
        )

        # Sanitize the tool response
        sanitized_response = sanitize_tool_response(hook_input.tool_response)

        # Create output dataclass
        hook_output = HookOutput(tool_response=sanitized_response, decision="approve")

        # Output JSON
        print(json.dumps(hook_output.to_dict()))

    except Exception as e:
        # On error, pass through original response and log error
        error_output = HookOutput(
            tool_response=input_dict.get("tool_response", {})
            if "input_dict" in locals()
            else {},
            decision="approve",
            reason=f"Sanitization error: {str(e)}",
        )
        print(json.dumps(error_output.to_dict()))
        sys.exit(1)
