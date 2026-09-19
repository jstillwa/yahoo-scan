import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS progress (
    uidvalidity TEXT PRIMARY KEY,
    last_uid INTEGER NOT NULL,
    cursor TEXT
);

CREATE TABLE IF NOT EXISTS email_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uidvalidity TEXT NOT NULL,
    uid INTEGER NOT NULL,
    processed_at TEXT NOT NULL,
    from_addr TEXT,
    subject TEXT,
    rspamd_score REAL,
    llm_label TEXT,
    recommended_action TEXT NOT NULL,
    final_action TEXT NOT NULL,
    mode TEXT NOT NULL,
    message_id TEXT,
    UNIQUE(uidvalidity, uid)
);

CREATE INDEX IF NOT EXISTS idx_processed_at ON email_actions(processed_at);
CREATE INDEX IF NOT EXISTS idx_final_action ON email_actions(final_action);
CREATE INDEX IF NOT EXISTS idx_from_addr ON email_actions(from_addr);
"""

class SeenStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self._migrate()
        with self.conn:
            self.conn.executescript(SCHEMA)

    def _migrate(self) -> None:
        """One-time upgrade of pre-multi-provider databases: add nullable
        cursor/message_id columns and prefix legacy uidvalidity keys with
        'yahoo:' so IMAP and Graph progress never collide."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(progress)")}
        if not cols:
            return  # fresh DB; SCHEMA creates everything
        if "cursor" not in cols:
            self.conn.execute("ALTER TABLE progress ADD COLUMN cursor TEXT")
        acols = {r[1] for r in self.conn.execute("PRAGMA table_info(email_actions)")}
        if acols and "message_id" not in acols:
            self.conn.execute("ALTER TABLE email_actions ADD COLUMN message_id TEXT")
        self._prefix_legacy_uidvalidity("progress")
        if acols:
            self._prefix_legacy_uidvalidity("email_actions")
        self.conn.commit()

    def _prefix_legacy_uidvalidity(self, table: str) -> None:
        self.conn.execute(
            f"UPDATE {table} SET uidvalidity = 'yahoo:' || uidvalidity "
            "WHERE uidvalidity NOT LIKE '%:%'"
        )

    def get_last_uid(self, uidvalidity: str) -> int:
        cur = self.conn.execute("SELECT last_uid FROM progress WHERE uidvalidity = ?", (uidvalidity,))
        row = cur.fetchone()
        return row[0] if row else 0

    def set_last_uid(self, uidvalidity: str, last_uid: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO progress(uidvalidity,last_uid) VALUES(?,?) "
                "ON CONFLICT(uidvalidity) DO UPDATE SET last_uid=excluded.last_uid",
                (uidvalidity, last_uid),
            )

    def get_cursor(self, uidvalidity: str) -> str:
        """Stored sync cursor (Graph delta token); '' when none."""
        cur = self.conn.execute(
            "SELECT cursor FROM progress WHERE uidvalidity = ?", (uidvalidity,)
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else ""

    def set_cursor(self, uidvalidity: str, cursor: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO progress(uidvalidity,last_uid,cursor) VALUES(?,0,?) "
                "ON CONFLICT(uidvalidity) DO UPDATE SET cursor=excluded.cursor",
                (uidvalidity, cursor),
            )

    def record_action(
        self,
        uidvalidity: str,
        uid: int,
        from_addr: str,
        subject: str,
        rspamd_score: float,
        llm_label: str,
        recommended_action: str,
        final_action: str,
        mode: str,
        message_id: str | None = None,
    ) -> None:
        """Record email processing action to database"""
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO email_actions
                (uidvalidity, uid, processed_at, from_addr, subject, rspamd_score,
                 llm_label, recommended_action, final_action, mode, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uidvalidity, uid) DO UPDATE SET
                    processed_at=excluded.processed_at,
                    from_addr=excluded.from_addr,
                    subject=excluded.subject,
                    rspamd_score=excluded.rspamd_score,
                    llm_label=excluded.llm_label,
                    recommended_action=excluded.recommended_action,
                    final_action=excluded.final_action,
                    mode=excluded.mode,
                    message_id=excluded.message_id
                """,
                (
                    uidvalidity,
                    uid,
                    datetime.now(UTC).isoformat(),
                    from_addr,
                    subject,
                    rspamd_score,
                    llm_label,
                    recommended_action,
                    final_action,
                    mode,
                    message_id,
                ),
            )

    def get_action(
        self, uidvalidity: str, message_id: str | None, uid: int | None = None
    ) -> dict[str, object] | None:
        """Look up one recorded action by Graph message_id or IMAP uid."""
        if message_id is not None:
            cur = self.conn.execute(
                "SELECT * FROM email_actions WHERE uidvalidity = ? AND message_id = ?",
                (uidvalidity, message_id),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM email_actions WHERE uidvalidity = ? AND uid = ?",
                (uidvalidity, uid),
            )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def get_domain_history(self, domain: str) -> dict[str, int]:
        """Get historical action counts for a specific domain"""
        if not domain:
            return {}

        cur = self.conn.execute(
            """
            SELECT final_action, COUNT(*) as count
            FROM email_actions
            WHERE from_addr LIKE ?
            GROUP BY final_action
            """,
            (f"%@{domain}",),
        )

        result = {}
        for action, count in cur.fetchall():
            result[action] = count

        return result
