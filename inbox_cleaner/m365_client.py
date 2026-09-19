"""Microsoft 365 mailbox client: MSAL client-credentials auth + Graph API.

Transport details (why not IMAP): M365 does not support app-only auth for
IMAP, so M365 uses Graph's delta query instead of UIDs. The deltaToken is
stored as a string cursor in the shared SQLite progress table; message ids
are Graph odata ids stored as text in email_actions.message_id.
"""

import os
import sys
import time
from typing import Any, Self

import msal
import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TENANT = os.getenv("M365_TENANT_ID", "")
_CLIENT_ID = os.getenv("M365_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("M365_CLIENT_SECRET", "")
M365_MAILBOX = os.getenv("M365_MAILBOX", "")
M365_MAX_RETRIES = 3


def m365_configured() -> bool:
    """Whether all M365 app-credential env vars are present."""
    return bool(_TENANT and _CLIENT_ID and _CLIENT_SECRET)


class M365Session:
    """Graph-backed Mailbox implementation for one M365 user mailbox.

    Deviates from the numeric-UID IMAP surface: message lists and moves take
    opaque Graph string ids, and incremental sync uses a deltaToken cursor
    (``search_since_cursor``) instead of UIDs.
    """

    name = "m365"

    def __init__(self, mailbox: str, transport: Any = None) -> None:
        self.mailbox = mailbox
        self._svc = transport or _GraphTransport()
        self._app: Any = None
        self._folder_cache: dict[str, str] = {}

    def __enter__(self) -> Self:
        self._app = msal.ConfidentialClientApplication(
            _CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{_TENANT}",
            client_credential=_CLIENT_SECRET,
        )
        if not self._acquire_token():
            raise RuntimeError("M365 auth failed: no access token")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        pass

    def _acquire_token(self) -> str | None:
        result = self._app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" in result:
            return str(result["access_token"])
        err = result.get("error_description") or result.get("error") or "unknown"
        print(f"M365 token error: {err}", file=sys.stderr)
        return None

    # ── Graph plumbing ───────────────────────────────────────────────

    def _token(self) -> str:
        tok = self._acquire_token()
        if not tok:
            raise RuntimeError("M365 auth failed: no access token")
        return tok

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def _request(
        self,
        method: str,
        url: str,
        body: dict[str, str] | None = None,
    ) -> requests.Response:
        last: Exception | None = None
        for attempt in range(M365_MAX_RETRIES):
            try:
                r = self._svc.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=body,
                    timeout=30,
                )
                if r.status_code == 401:  # token expired mid-run
                    self._acquire_token()
                    r = self._svc.request(
                        method, url, headers=self._headers(), json=body, timeout=30
                    )
                r.raise_for_status()
                return r
            except requests.RequestException as exc:
                last = exc
                if attempt < M365_MAX_RETRIES - 1:
                    time.sleep(2**attempt)
        raise LookupError(f"Graph {method} failed after retries: {last}")

    # ── Mailbox protocol (shared IMAP-like surface) ─────────────────

    def select_mailbox(self, name: str) -> None:
        """Selection is implicit in Graph URLs; folder cache is per-run."""
        self._folder_cache = {}

    def get_uidvalidity(self, name: str) -> str:
        return f"m365:{self.mailbox}"

    def search_since_uid(self, last_uid: int) -> list[int]:
        raise NotImplementedError(
            "M365 tracks changes with a delta-token cursor; use "
            "search_since_cursor() + get_cursor/set_cursor in cli."
        )

    def search_since_cursor(
        self, cursor: str
    ) -> tuple[list[dict[str, str]], str]:
        """Return (changed messages, next delta token).

        Each message dict has keys: id, subject, from, change — where change
        is 'upserted' or 'deleted' (Graph tombstones via @removed annotation).
        The returned token is the raw $deltatoken for the next run.
        """
        url: str | None = f"{GRAPH_BASE}/users/{self.mailbox}/mailFolders/inbox/messages/delta"
        if cursor:
            url = f"{url}?$deltatoken={cursor}"
        out: list[dict[str, str]] = []
        next_token = ""
        while url:
            r = self._request("GET", url)
            payload = r.json()
            for m in payload.get("value", []):
                frm = (m.get("from") or {}).get("emailAddress", {}).get("address", "")
                out.append(
                    {
                        "id": str(m["id"]),
                        "subject": str(m.get("subject") or ""),
                        "from": frm,
                        "change": "deleted" if "@removed" in m else "upserted",
                    }
                )
            next_link: str | None = payload.get("@odata.nextLink")
            if next_link:
                url = next_link
                continue
            delta_link = payload.get("@odata.deltaLink", "")
            next_token = _delta_token_from_link(delta_link)
            url = None
        return (out, next_token)

    def fetch_rfc822(self, uid: int) -> bytes:
        raise NotImplementedError("M365 uses fetch_message_rfc822(message_id)")

    def fetch_message_rfc822(self, message_id: str) -> bytes:
        r = self._request(
            "GET", f"{GRAPH_BASE}/users/{self.mailbox}/messages/{message_id}/$value"
        )
        return r.content

    def fetch_headers(self, uid: int) -> str:
        raise NotImplementedError("M365 headers come from the RFC822 bytes")

    def ensure_folder(self, name: str) -> None:
        self._folder_id(name, create=True)

    def move_to_folder(self, uid: int, dest: str) -> None:
        raise NotImplementedError("M365 uses move_message(message_id, dest)")

    def move_message(self, message_id: str, dest: str) -> None:
        self._request(
            "POST",
            f"{GRAPH_BASE}/users/{self.mailbox}/messages/{message_id}/move",
            {"destinationId": self._folder_id(dest, create=True)},
        )

    # ── internal folder handling ─────────────────────────────────────

    def _folder_id(self, display_name: str, create: bool = False) -> str:
        """Resolve a folder display name to a Graph folder id (cached)."""
        key = display_name.lower()
        if key in self._folder_cache:
            return self._folder_cache[key]
        wellknown = {
            "inbox": "inbox",
            "drafts": "drafts",
            "sentitems": "sentitems",
            "deleteditems": "deleteditems",
            "junkemail": "junkemail",
        }
        if key in wellknown:
            self._folder_cache[key] = wellknown[key]
            return wellknown[key]
        r = self._request(
            "GET", f"{GRAPH_BASE}/users/{self.mailbox}/mailFolders/inbox/childFolders"
        )
        for f in r.json().get("value", []):
            if str(f.get("displayName", "")).lower() == key:
                fid = str(f["id"])
                self._folder_cache[key] = fid
                return fid
        if not create:
            raise RuntimeError(f"M365 folder not found: {display_name}")
        created = self._request(
            "POST",
            f"{GRAPH_BASE}/users/{self.mailbox}/mailFolders/inbox/childFolders",
            {"displayName": display_name},
        )
        fid = str(created.json()["id"])
        self._folder_cache[key] = fid
        return fid


def _delta_token_from_link(delta_link: str) -> str:
    """Extract the raw $deltatoken value from an @odata.deltaLink URL.

    Graph hands back full URLs like
    .../messages/delta?$deltatoken=<opaque>. We store only the token so the
    progress row stays small; it is reattached to the fresh delta URL on the
    next run.
    """
    marker = "$deltatoken="
    idx = delta_link.find(marker)
    if idx == -1:
        return ""
    return delta_link[idx + len(marker) :].split("&")[0]


class _GraphTransport:
    """Default HTTP transport; injectable in tests to mock Graph."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        json: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> requests.Response:
        return requests.request(method, url, headers=headers, json=json, timeout=timeout)
