"""Run the synthetic engineering load profile against a running local Lorekeeper."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(base: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(base.rstrip("/") + path, data=body,
                      headers={"Content-Type": "application/json"} if body is not None else {},
                      method="POST" if body is not None else "GET")
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic raid/load profile. This is not Twitch traffic.")
    parser.add_argument("--base-url", default="http://localhost:8787")
    parser.add_argument("--duration", type=int, default=60, help="Target duration in seconds (1..60)")
    parser.add_argument("--rate", type=int, default=10000, help="Target event append rate per second (1..10000)")
    parser.add_argument("--profile", choices=("raid", "steady"), default="raid")
    args = parser.parse_args()
    if not 1 <= args.duration <= 60 or not 1 <= args.rate <= 10000:
        parser.error("duration must be 1..60 and rate must be 1..10000")
    count = args.duration * args.rate
    print(f"Starting synthetic {args.profile} load: {count:,} events, target {args.rate:,}/s for {args.duration}s")
    print("This measures the engineering stress path; it does not simulate or replace the live Twitch demo.")
    try:
        request_json(args.base_url, "/api/ready")
        request_json(args.base_url, "/api/load-test", {"events": count, "rate": args.rate, "profile": args.profile})
        result = None
        while True:
            state = request_json(args.base_url, "/api/state")
            status = state.get("load_test", {})
            if status.get("error"):
                raise RuntimeError(status["error"])
            if status.get("result") is not None:
                result = status["result"]
                break
            if not status.get("running"):
                raise RuntimeError("Load test stopped without producing a result")
            time.sleep(1)
    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        print(f"Load test failed: {exc}")
        return 1

    result["run_at_local"] = datetime.now().astimezone().isoformat(timespec="seconds")
    results_dir = Path("load-test-results")
    results_dir.mkdir(exist_ok=True)
    output = results_dir / f"{args.profile}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Saved results: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
