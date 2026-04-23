import sys
import time

import requests


def check_message(rspamd_url: str, raw_email: bytes) -> dict[str, object]:
    """
    Rspamd HTTP /checkv2: returns JSON with score/action.
    Retries up to 3 times with backoff on transient failures.
    """
    headers = {"Content-Type": "message/rfc822"}
    delays = [1, 2]  # delays between attempts: 1s, then 2s

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(rspamd_url, data=raw_email, headers=headers, timeout=20)
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {"score": 0.0, "action": "noaction"}
        except requests.RequestException as e:
            last_exc = e
            if attempt < 2:
                print(
                    f"  WARNING: Rspamd request failed (attempt {attempt + 1}/3): {e}",
                    file=sys.stderr,
                )
                time.sleep(delays[attempt])

    print(
        f"  WARNING: Rspamd unavailable after 3 attempts: {last_exc}",
        file=sys.stderr,
    )
    print("  Defaulting to safe score (0.0 / noaction).", file=sys.stderr)
    return {"score": 0.0, "action": "noaction"}
