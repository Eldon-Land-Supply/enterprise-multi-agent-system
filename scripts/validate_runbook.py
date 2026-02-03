#!/usr/bin/env python3
import json
import sys


def validate_runbook(path: str) -> int:
    """Validate a runbook JSON file against minimal schema requirements."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Failed to read runbook: {e}")
        return 1

    required_fields = ["schemaVersion", "runbookId", "scopes", "steps"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        print(f"Missing required fields: {', '.join(missing)}")
        return 1

    if not isinstance(data["steps"], list) or not data["steps"]:
        print("The 'steps' field must be a non-empty array.")
        return 1

    print(f"Runbook '{data.get('runbookId')}' is valid.")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_runbook.py <runbook.json>")
        return 1
    return validate_runbook(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
