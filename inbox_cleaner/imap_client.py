import imaplib
import ssl

class ImapSession:
    def __init__(self, host, port, user, app_password):
        self.host = host
        self.port = port
        self.user = user
        self.app_password = app_password
        self.conn = None

    def __enter__(self):
        ctx = ssl.create_default_context()
        self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx)
        # Yahoo requires OAuth2 or App Password (basic auth disabled).
        self.conn.login(self.user, self.app_password)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.conn.logout()
        except Exception:
            pass

    def _ok(self, typ):
        if typ != "OK":
            raise RuntimeError("IMAP command failed")

    def select_mailbox(self, name):
        typ, _ = self.conn.select(name, readonly=False)
        self._ok(typ)

    def get_uidvalidity(self, name):
        typ, data = self.conn.status(name, "(UIDVALIDITY)")
        self._ok(typ)
        # data example: [b'INBOX (UIDVALIDITY 3)']
        s = data[0].decode("utf-8")
        return s.split("UIDVALIDITY", 1)[1].strip(" )")

    def search_since_uid(self, last_uid: int):
        # Search all, then filter by uid > last_uid
        typ, data = self.conn.uid("SEARCH", None, "ALL")
        self._ok(typ)
        if not data or data[0] is None:
            return []
        uids = [int(x) for x in data[0].split()]
        return [u for u in uids if u > last_uid]

    def fetch_rfc822(self, uid: int) -> bytes:
        # Use BODY.PEEK[] instead of RFC822 to avoid marking message as read
        typ, data = self.conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        self._ok(typ)
        return data[0][1]

    def fetch_headers(self, uid: int) -> str:
        typ, data = self.conn.uid("FETCH", str(uid), "(BODY.PEEK[HEADER])")
        self._ok(typ)
        return data[0][1].decode("utf-8", errors="replace")

    def _quote_folder(self, name: str) -> str:
        """Quote folder name if it contains spaces"""
        if ' ' in name:
            return f'"{name}"'
        return name

    def ensure_folder(self, name: str):
        # Try to create. If exists, ignore.
        # Quote folder name if it contains spaces
        quoted_name = self._quote_folder(name)
        typ, _ = self.conn.create(quoted_name)
        if typ not in ("OK", "NO", "BAD"):
            self._ok(typ)
        # If Yahoo already has it, create will NO. That is fine.

    def move_to_folder(self, uid: int, dest: str):
        # MOVE is not guaranteed. Use COPY + delete.
        # Quote folder name if it contains spaces
        quoted_dest = self._quote_folder(dest)
        typ, _ = self.conn.uid("COPY", str(uid), quoted_dest)
        self._ok(typ)
        self.conn.uid("STORE", str(uid), "+FLAGS", r"(\Deleted)")
        self.conn.expunge()
