"""Mailbox abstraction shared by IMAP (Yahoo) and Graph (M365) providers."""

from typing import Protocol, Self


class Mailbox(Protocol):
    """Protocol matching the existing ImapSession surface.

    Implementations are context managers usable exactly as ImapSession is
    used in cli.py. M365 implementations may return opaque string cursors
    instead of numeric UIDs where noted.
    """

    name: str

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    def select_mailbox(self, name: str) -> None: ...

    def get_uidvalidity(self, name: str) -> str: ...

    def search_since_uid(self, last_uid: int) -> list[int]: ...

    def fetch_rfc822(self, uid: int) -> bytes: ...

    def fetch_headers(self, uid: int) -> str: ...

    def ensure_folder(self, name: str) -> None: ...

    def move_to_folder(self, uid: int, dest: str) -> None: ...
