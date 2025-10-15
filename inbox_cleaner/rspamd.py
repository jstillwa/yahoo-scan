import requests

def check_message(rspamd_url: str, raw_email: bytes) -> dict:
    """
    Rspamd HTTP /checkv2: returns JSON with score/action
    """
    headers = {"Content-Type": "message/rfc822"}
    r = requests.post(rspamd_url, data=raw_email, headers=headers, timeout=20)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"score": 0.0, "action": "noaction"}
