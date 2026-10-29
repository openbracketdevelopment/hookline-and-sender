import argparse
import os
import sys
import time
from uuid import uuid4

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local delivery demo")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument(
        "--simulate-retries",
        action="store_true",
        help="Make the local receiver fail twice before succeeding",
    )
    args = parser.parse_args()
    base_url = os.getenv("DEMO_API_URL", "http://127.0.0.1:8000").rstrip("/")
    api_key = os.getenv("API_KEY")
    if not api_key:
        print("API_KEY is missing; use the local .env file.", file=sys.stderr)
        return 2
    receiver_url = os.getenv(
        "DEMO_RECEIVER_CONTROL_URL", "http://127.0.0.1:8081"
    ).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    request_body = {
        "source": "payload-demo",
        "source_event_id": f"publish-page-about-{uuid4()}",
        "type": "content.published",
        "destination_id": "demo-receiver",
        "data": {
            "collection": "pages",
            "document_id": "page_123",
            "slug": "about",
        },
    }

    with httpx.Client(timeout=5.0) as client:
        reset = client.post(
            f"{receiver_url}/demo/reset",
            params={"failures": 2 if args.simulate_retries else 0},
        )
        reset.raise_for_status()
        accepted = client.post(
            f"{base_url}/api/v1/events", json=request_body, headers=headers
        )
        accepted.raise_for_status()
        ids = accepted.json()
        print(f"Accepted event {ids['event_id']}; delivery {ids['delivery_id']}")

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            response = client.get(f"{base_url}{ids['status_url']}", headers=headers)
            response.raise_for_status()
            delivery = response.json()
            if delivery["status"] in {"succeeded", "failed"}:
                for attempt in delivery["attempts"]:
                    print(
                        f"Attempt {attempt['attempt_number']}: "
                        f"{attempt['outcome']} (HTTP {attempt['http_status']})"
                    )
                if delivery["status"] == "succeeded":
                    print("Demo completed successfully.")
                    return 0
                print("Delivery reached a terminal failure.", file=sys.stderr)
                return 1
            time.sleep(0.5)

    print(f"Demo did not complete within {args.timeout:g} seconds.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
