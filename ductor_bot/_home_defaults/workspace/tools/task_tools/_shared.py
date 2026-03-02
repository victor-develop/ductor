"""Shared HTTP helpers for task tool scripts."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def get_api_url(path: str) -> str:
    """Build internal API URL from environment."""
    port = os.environ.get("DUCTOR_INTERAGENT_PORT", "8799")
    host = os.environ.get("DUCTOR_INTERAGENT_HOST", "127.0.0.1")
    return f"http://{host}:{port}{path}"


def post_json(url: str, body: dict[str, object], *, timeout: int = 300) -> dict[str, object]:
    """POST JSON to internal API, return parsed response."""
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())  # type: ignore[no-any-return]
    except urllib.error.URLError as e:
        print(f"Error: Cannot reach task API at {url}: {e}", file=sys.stderr)
        print("Make sure the Ductor bot is running with tasks enabled.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def get_json(url: str, *, timeout: int = 10) -> dict[str, object]:
    """GET JSON from internal API, return parsed response."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())  # type: ignore[no-any-return]
    except urllib.error.URLError as e:
        print(f"Error: Cannot reach task API at {url}: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
