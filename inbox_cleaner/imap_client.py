import imaplib
import ssl

MAX_RETRIES = 3


class ImapSession:
    def __init__(self, host: str, port: int, user: str, app_password: str) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.app_password = app_password
        self.conn: imaplib.IMAP4_SSL | None = None
        self._selected_mailbox: str | None = None

    def __enter__(self) -> "ImapSession":
        self._connect()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> None:
        try:
            if self.conn:
                self.conn.logout()
        except Exception:
            pass

    def _connect(self) -> None:
        ctx = ssl.create_default_context()
        self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx)
        self.conn.login(self.user, self.app_password)
        if self._selected_mailbox:
            self.conn.select(self._selected_mailbox, readonly=False)

    def reconnect(self) -> None:
        """Drop the current connection and establish a fresh one."""
        try:
            if self.conn:
                self.conn.logout()
        except Exception:
            pass
        self._connect()

    def _ok(self, typ: str) -> None:
        if typ != "OK":
            raise RuntimeError("IMAP command failed")

    def select_mailbox(self, name: str) -> None:
        self._selected_mailbox = name
        typ, _ = self.conn.select(name, readonly=False)
        self._ok(typ)

    def get_uidvalidity(self, name: str) -> str:
        typ, data = self.conn.status(name, "(UIDVALIDITY)")
        self._ok(typ)
        # data example: [b'INBOX (UIDVALIDITY 3)']
        s = data[0].decode("utf-8")
        return s.split("UIDVALIDITY", 1)[1].strip(" )")

    def search_since_uid(self, last_uid: int) -> list[int]:
        def _search() -> list[int]:
            typ, data = self.conn.uid("SEARCH", None, "ALL")
            self._ok(typ)
            if not data or data[0] is None:
                return []
            uids = [int(x) for x in data[0].split()]
            return [u for u in uids if u > last_uid]
        return self._retry_on_abort(_search)

    def _retry_on_abort(self, fn: "callable") -> object:
        """Retry an IMAP operation up to MAX_RETRIES times on server disconnect."""
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return fn()
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    print(f"  ⚠ IMAP connection lost, reconnecting (attempt {attempt + 2}/{MAX_RETRIES})...")
                    self.reconnect()
        raise last_exc  # type: ignore[misc]

    def _validate_fetch_data(self, data: list[object], uid: int, part: str) -> None:
        """Validate that IMAP FETCH returned a usable response."""
        if not data or data[0] is None:
            raise RuntimeError(
                f"IMAP FETCH {part} for UID {uid} returned empty data"
            )
        if not isinstance(data[0], tuple) or len(data[0]) < 2:
            raise RuntimeError(
                f"IMAP FETCH {part} for UID {uid} returned unexpected shape: {type(data[0])}"
            )

    def fetch_rfc822(self, uid: int) -> bytes:
        # Use BODY.PEEK[] instead of RFC822 to avoid marking message as read
        def _fetch() -> bytes:
            typ, data = self.conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
            self._ok(typ)
            self._validate_fetch_data(data, uid, "BODY.PEEK[]")
            return data[0][1]
        return self._retry_on_abort(_fetch)

    def fetch_headers(self, uid: int) -> str:
        def _fetch() -> str:
            typ, data = self.conn.uid("FETCH", str(uid), "(BODY.PEEK[HEADER])")
            self._ok(typ)
            self._validate_fetch_data(data, uid, "BODY.PEEK[HEADER]")
            return data[0][1].decode("utf-8", errors="replace")
        return self._retry_on_abort(_fetch)

    def _quote_folder(self, name: str) -> str:
        """Quote folder name if it contains spaces"""
        if ' ' in name:
            return f'"{name}"'
        return name

    def ensure_folder(self, name: str) -> None:
        # Try to create. If exists, ignore.
        # Quote folder name if it contains spaces
        quoted_name = self._quote_folder(name)
        typ, _ = self.conn.create(quoted_name)
        if typ not in ("OK", "NO", "BAD"):
            self._ok(typ)
        # If Yahoo already has it, create will NO. That is fine.

    def move_to_folder(self, uid: int, dest: str) -> None:
        # Try MOVE extension first, fall back to COPY + STORE + EXPUNGE
        quoted_dest = self._quote_folder(dest)
        def _move() -> None:
            try:
                typ, _ = self.conn.uid("MOVE", str(uid), quoted_dest)
                if typ == "OK":
                    return
            except (imaplib.IMAP4.error, AttributeError):
                pass  # Server doesn't support MOVE, fall back
            # Fallback: COPY + flag deleted + expunge
            typ, _ = self.conn.uid("COPY", str(uid), quoted_dest)
            self._ok(typ)
            typ, _ = self.conn.uid("STORE", str(uid), "+FLAGS", r"(\Deleted)")
            self._ok(typ)
            self.conn.expunge()
        self._retry_on_abort(_move)
