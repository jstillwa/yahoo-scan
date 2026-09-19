"""Tests for Mailbox protocol conformance, M365 Graph client, and db provider prefixing."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from inbox_cleaner import cli as cli_mod
from inbox_cleaner.db import SeenStore
from inbox_cleaner.imap_client import ImapSession
from inbox_cleaner.m365_client import M365Session
from inbox_cleaner.mailbox import Mailbox

# ── Test doubles ─────────────────────────────────────────────────────────


class FakeMsal:
    def acquire_token_for_client(self, scopes: list[str]) -> dict[str, str]:
        return {"access_token": "testtoken"}


class FakeTokenApp:
    """Injected in place of the MSAL app object."""

    def acquire_token_for_client(self, scopes: list[str]) -> dict[str, str]:
        return {"access_token": "testtoken"}


def make_transport(handler):
    """Build a fake Graph HTTP transport.

    handler(method, url, json) -> payload dict, or ("raw", bytes) for $value.
    """

    class R:
        status_code = 200

        def __init__(self, payload: object) -> None:
            self._p = payload
            self.content = payload[1] if isinstance(payload, tuple) else b"{}"  # type: ignore[index]

        def raise_for_status(self) -> None:
            pass

        def json(self) -> object:
            return self._p if not isinstance(self._p, tuple) else {}

    class FakeTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def request(self, method: str, url: str, headers: dict[str, str] | None = None,
                    json: dict[str, str] | None = None, timeout: int = 30) -> R:
            self.calls.append((method, url))
            return R(handler(method, url, json))

    return FakeTransport()


# ── Protocol conformance ─────────────────────────────────────────────────


class TestProtocolConformance:
    def test_imap_session_is_mailbox(self) -> None:
        session: Mailbox = ImapSession("imap.example.com", 993, "u", "p")
        assert session.name == "yahoo"

    def test_m365_session_is_mailbox(self) -> None:
        session: Mailbox = M365Session("user@example.com", transport=object())
        assert session.name == "m365"


# ── M365 Graph client ────────────────────────────────────────────────────


class TestM365DeltaSearch:
    def _session(self, handler) -> tuple[M365Session, object]:
        transport = make_transport(handler)
        session = M365Session("user@example.com", transport=transport)
        session._app = FakeMsal()
        return session, transport

    def test_first_run_returns_messages_and_token(self) -> None:
        calls = {"n": 0}

        def paginated(method: str, url: str, json: object) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "value": [{"id": "AAA", "subject": "Hello",
                               "from": {"emailAddress": {"address": "a@b.com"}}}],
                    "@odata.nextLink": "https://g/page2",
                }
            return {"value": [], "@odata.deltaLink": "https://g/delta?$deltatoken=NEXT"}

        transport = make_transport(paginated)
        session = M365Session("user@example.com", transport=transport)
        session._app = FakeMsal()
        msgs, token = session.search_since_cursor("")
        assert [(m["id"], m["change"]) for m in msgs] == [("AAA", "upserted")]
        assert token == "NEXT"

    def test_deleted_tombstoned_messages_flagged(self) -> None:
        def handler(method: str, url: str, json: object) -> object:
            return {
                "value": [
                    {"id": "GONE", "@removed": {"reason": "deleted"},
                     "subject": None, "from": None},
                ],
                "@odata.deltaLink": "https://g/delta?$deltatoken=TK",
            }

        session, _ = self._session(handler)
        msgs, token = session.search_since_cursor("")
        assert msgs == [{"id": "GONE", "subject": "", "from": "", "change": "deleted"}]
        assert token == "TK"

    def test_cursor_roundtrip_reattaches_token(self) -> None:
        state = {"pages": [
            {"value": [], "@odata.deltaLink": "https://g/delta?$deltatoken=T2"},
        ]}

        def handler(method: str, url: str, json: object) -> object:
            assert "$deltatoken=T1" in url  # stored cursor reattached
            return state["pages"].pop(0)

        session, _ = self._session(handler)
        _msgs, token = session.search_since_cursor("T1")
        assert token == "T2"


class TestM365FoldersAndMoves:
    def _session(self) -> tuple[M365Session, object]:
        def handler(method: str, url: str, json: object) -> object:
            if "/$value" in url:
                return ("raw", b"From: a@b.com\r\nSubject: hi\r\n\r\nbody")
            if "/move" in url:
                return {"@odata.context": "x"}
            if "childFolders" in url and method == "POST":
                return {"id": "CREATED", "displayName": (json or {}).get("displayName")}  # type: ignore[union-attr]
            return {"value": [{"id": "FID", "displayName": "Promotional"}]}

        transport = make_transport(handler)
        session = M365Session("user@example.com", transport=transport)
        session._app = FakeMsal()
        return session, transport

    def test_ensure_folder_creates_missing(self) -> None:
        session, transport = self._session()
        session.ensure_folder("Newsletters")
        assert any(c[0] == "POST" and "childFolders" in c[1] for c in transport.calls)  # type: ignore[attr-defined]
        assert session._folder_id("Newsletters") == "CREATED"

    def test_ensure_folder_reuses_existing(self) -> None:
        session, transport = self._session()
        session.ensure_folder("Promotional")  # exists in fake listing
        posts = [c for c in transport.calls if c[0] == "POST" and "childFolders" in c[1]]  # type: ignore[attr-defined]
        assert posts == []

    def test_wellknown_folder_no_http(self) -> None:
        session, transport = self._session()
        before = len(transport.calls)  # type: ignore[attr-defined]
        assert session._folder_id("INBOX") == "inbox"
        assert len(transport.calls) == before  # type: ignore[attr-defined]

    def test_move_message_resolves_folder(self) -> None:
        session, transport = self._session()
        session.move_message("MID", "Newsletters")
        move_calls = [c for c in transport.calls if "/messages/MID/move" in c[1]]  # type: ignore[attr-defined]
        assert move_calls, transport.calls

    def test_fetch_mime_returns_bytes(self) -> None:
        session, _ = self._session()
        raw = session.fetch_message_rfc822("MID")
        assert raw.startswith(b"From: a@b.com")


class TestM365Config:
    def test_configured_false_when_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:

        import inbox_cleaner.m365_client as m
        monkeypatch.setattr(m, "_TENANT", "")
        monkeypatch.setattr(m, "_CLIENT_ID", "")
        monkeypatch.setattr(m, "_CLIENT_SECRET", "")
        assert m.m365_configured() is False

    def test_uid_methods_not_supported(self) -> None:
        session = M365Session("user@example.com", transport=object())
        with pytest.raises(NotImplementedError):
            session.search_since_uid(0)
        with pytest.raises(NotImplementedError):
            session.fetch_rfc822(1)
        with pytest.raises(NotImplementedError):
            session.move_to_folder(1, "X")


# ── db: provider-prefixed keys + cursor ──────────────────────────────────


class TestDbPrefixing:
    @pytest.fixture()
    def store(self, tmp_path: pytest.TempPathFactory) -> SeenStore:
        return SeenStore(str(tmp_path / "t.sqlite"))  # type: ignore[operator]

    def test_cursors_are_independent_per_uidvalidity(self, store: SeenStore) -> None:
        store.set_cursor("m365:user@x.com", "TOKEN-1")
        store.set_last_uid("yahoo:123", 7)
        assert store.get_cursor("m365:user@x.com") == "TOKEN-1"
        assert store.get_cursor("yahoo:123") == ""
        assert store.get_last_uid("yahoo:123") == 7
        store.set_cursor("m365:user@x.com", "TOKEN-2")
        assert store.get_cursor("m365:user@x.com") == "TOKEN-2"

    def test_record_action_with_message_id(self, store: SeenStore) -> None:
        store.record_action(
            uidvalidity="m365:user@x.com",
            uid=5,
            message_id="ABC-graph-id",
            from_addr="a@b.com",
            subject="t",
            rspamd_score=0.0,
            llm_label="normal",
            recommended_action="keep",
            final_action="keep",
            mode="auto",
        )
        rec = store.get_action("m365:user@x.com", "ABC-graph-id")
        assert rec is not None
        assert rec["final_action"] == "keep"
        assert rec["message_id"] == "ABC-graph-id"

    def test_legacy_schema_migration_adds_columns(self, tmp_path: pytest.TempPathFactory) -> None:
        import sqlite3

        db_path = str(tmp_path / "legacy.sqlite")  # type: ignore[operator]
        # Simulate a pre-upgrade DB
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE progress (uidvalidity TEXT PRIMARY KEY, last_uid INTEGER NOT NULL);
            CREATE TABLE email_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uidvalidity TEXT NOT NULL, uid INTEGER NOT NULL,
                processed_at TEXT NOT NULL, from_addr TEXT, subject TEXT,
                rspamd_score REAL, llm_label TEXT,
                recommended_action TEXT NOT NULL, final_action TEXT NOT NULL,
                mode TEXT NOT NULL, UNIQUE(uidvalidity, uid)
            );
            INSERT INTO progress VALUES ('42', 100);
            INSERT INTO email_actions (uidvalidity, uid, processed_at, from_addr,
                subject, rspamd_score, llm_label, recommended_action, final_action, mode)
            VALUES ('42', 7, '2024-01-01', 'a@b.com', 's', 0.0, 'normal', 'keep', 'keep', 'auto');
            """
        )
        conn.commit()
        conn.close()

        store = SeenStore(db_path)  # triggers migration
        assert store.get_last_uid("yahoo:42") == 100
        rec = store.get_action("yahoo:42", None, uid=7)
        assert rec is not None
        assert rec["final_action"] == "keep"

    def test_prefix_helper(self) -> None:
        assert cli_mod._prefixed_key("yahoo", "42") == "yahoo:42"
        assert cli_mod._prefixed_key("m365", "m365:x") == "m365:x"


class TestStableInt:
    def test_deterministic_31bit(self) -> None:
        a = cli_mod._stable_int("AAAA-graph-id")
        b = cli_mod._stable_int("AAAA-graph-id")
        c = cli_mod._stable_int("BBBB-graph-id")
        assert a == b
        assert a != c
        assert 0 <= a < 2**31

    def test_record_action_message_id_optional(self, tmp_path: pytest.TempPathFactory) -> None:
        store = SeenStore(str(tmp_path / "t2.sqlite"))  # type: ignore[operator]
        store.record_action(
            uidvalidity="yahoo:1", uid=5, message_id=None, from_addr="a@b.com",
            subject="t", rspamd_score=0.0, llm_label="normal",
            recommended_action="keep", final_action="keep", mode="auto",
        )
        rec = store.get_action("yahoo:1", None, uid=5)
        assert rec is not None and rec["message_id"] is None
