import sqlite3
from pathlib import Path
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS progress (
    uidvalidity TEXT PRIMARY KEY,
    last_uid INTEGER NOT NULL
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
        with self.conn:
            self.conn.executescript(SCHEMA)

    def get_last_uid(self, uidvalidity: str) -> int:
        cur = self.conn.execute("SELECT last_uid FROM progress WHERE uidvalidity = ?", (uidvalidity,))
        row = cur.fetchone()
        return row[0] if row else 0

    def set_last_uid(self, uidvalidity: str, last_uid: int):
        with self.conn:
            self.conn.execute(
                "INSERT INTO progress(uidvalidity,last_uid) VALUES(?,?) "
                "ON CONFLICT(uidvalidity) DO UPDATE SET last_uid=excluded.last_uid",
                (uidvalidity, last_uid),
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
    ):
        """Record email processing action to database"""
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO email_actions
                (uidvalidity, uid, processed_at, from_addr, subject, rspamd_score,
                 llm_label, recommended_action, final_action, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uidvalidity, uid) DO UPDATE SET
                    processed_at=excluded.processed_at,
                    from_addr=excluded.from_addr,
                    subject=excluded.subject,
                    rspamd_score=excluded.rspamd_score,
                    llm_label=excluded.llm_label,
                    recommended_action=excluded.recommended_action,
                    final_action=excluded.final_action,
                    mode=excluded.mode
                """,
                (
                    uidvalidity,
                    uid,
                    datetime.utcnow().isoformat(),
                    from_addr,
                    subject,
                    rspamd_score,
                    llm_label,
                    recommended_action,
                    final_action,
                    mode,
                ),
            )

    def get_domain_history(self, domain: str) -> dict:
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
            (f"%@{domain}%",),
        )

        result = {}
        for action, count in cur.fetchall():
            result[action] = count

        return result
