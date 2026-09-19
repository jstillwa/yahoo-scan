#!/usr/bin/env python3

import argparse
import os
import re
import sys
from email import message_from_bytes
from email.header import decode_header

from dotenv import load_dotenv

from .classify import classify_message
from .db import SeenStore
from .imap_client import ImapSession
from .mailbox import Mailbox
from .rspamd import check_message

# Load .env file from current directory or parent directories
load_dotenv()

IMAP_HOST = os.getenv("IMAP_HOST", "imap.mail.yahoo.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
YAHOO_EMAIL = os.getenv("YAHOO_EMAIL")
YAHOO_APP_PASSWORD = os.getenv("YAHOO_APP_PASSWORD")
M365_TENANT_ID = os.getenv("M365_TENANT_ID")
M365_CLIENT_ID = os.getenv("M365_CLIENT_ID")
M365_CLIENT_SECRET = os.getenv("M365_CLIENT_SECRET")
M365_MAILBOX = os.getenv("M365_MAILBOX")
M365_MAILBOX_FOLDER = os.getenv("M365_MAILBOX_FOLDER", "INBOX")
PROVIDERS = os.getenv("PROVIDERS")  # unset = auto-detect from credentials
MAILBOX = os.getenv("MAILBOX", "INBOX")
DEST_FOLDER = os.getenv("DEST_FOLDER", "Promotional")
TRASH_FOLDER = os.getenv("TRASH_FOLDER", "Bulk Mail")
SQLITE_PATH = os.getenv("SQLITE_PATH", "./state.sqlite")
RSPAMD_URL = os.getenv("RSPAMD_URL", "http://127.0.0.1:11333/checkv2")
RSPAMD_SPAM_SCORE = float(os.getenv("RSPAMD_SPAM_SCORE", "6.0"))
RSPAMD_TRASH_SCORE = float(os.getenv("RSPAMD_TRASH_SCORE", "7.0"))
INTERACTIVE = os.getenv("INTERACTIVE", "true").lower() in ("true", "1", "yes")
HISTORY_WEIGHT = float(os.getenv("HISTORY_WEIGHT", "0.3"))
HISTORY_MIN_SAMPLES = int(os.getenv("HISTORY_MIN_SAMPLES", "3"))

def decode_email_header(header_value: str) -> str:
    """Decode email header value (handles encoded headers)"""
    if not header_value:
        return ""
    decoded_parts = decode_header(header_value)
    result = []
    for content, encoding in decoded_parts:
        if isinstance(content, bytes):
            # Handle unknown or invalid encodings
            if encoding and encoding.lower() not in ('unknown-8bit', 'unknown'):
                try:
                    result.append(content.decode(encoding, errors='replace'))
                except (LookupError, UnicodeDecodeError):
                    # Fall back to utf-8 if encoding is invalid
                    result.append(content.decode('utf-8', errors='replace'))
            else:
                # Default to utf-8 for unknown encodings
                result.append(content.decode('utf-8', errors='replace'))
        else:
            result.append(content)
    return ''.join(result)

def extract_email_info(raw_email: bytes) -> tuple[str, str]:
    """Extract subject and from fields from raw email"""
    msg = message_from_bytes(raw_email)
    subject = decode_email_header(msg.get('Subject', '(No Subject)'))
    from_addr = decode_email_header(msg.get('From', '(Unknown)'))
    return subject, from_addr

def extract_domain(from_addr: str) -> str:
    """Extract domain from email address (e.g., 'Name <user@example.com>' -> 'example.com')"""
    # Handle formats: "Name <email@domain.com>" or "email@domain.com"
    match = re.search(r'[\w\.-]+@([\w\.-]+)', from_addr)
    if match:
        return match.group(1).lower()
    return ""

def calculate_historical_bias(domain_history: dict[str, int], min_samples: int = 3) -> dict[str, float] | None:
    """Calculate historical action percentages for a domain"""
    if not domain_history:
        return None

    total = sum(domain_history.values())
    if total < min_samples:
        return None

    return {
        "trash": domain_history.get("trash", 0) / total,
        "promotional": domain_history.get("promotional", 0) / total,
        "skip": domain_history.get("skip", 0) / total,
        "total": total,
    }

def decide_action(
    rspamd_result: dict[str, object],
    llm_label: str,
    score_threshold: float,
    spam_threshold: float = 10.0,
    domain_history: dict[str, int] | None = None,
    history_weight: float = 0.3,
    history_min_samples: int = 3,
) -> str:
    """Decide what action to take based on rspamd, LLM, and historical results.

    Priority order:
    1. Very high-confidence spam (rspamd reject / extreme score) — never overridden
    2. Strong user history (≥5 samples, ≥80% agreement) — overrides medium signals
    3. Medium rspamd / LLM signals — promotional or trash
    4. Moderate history signals — tiebreaker for borderline cases
    5. Default → keep
    """
    score = float(rspamd_result.get("score", 0.0))
    action = (rspamd_result.get("action") or "").lower()

    hist_bias = calculate_historical_bias(domain_history, history_min_samples)

    # ── 1. Very high-confidence spam — history never overrides ──
    if action == "reject" or score >= spam_threshold:
        return "trash"

    # ── 2. Strong user history overrides medium-confidence signals ──
    if hist_bias and hist_bias["total"] >= max(history_min_samples, 5):
        # User has consistently kept emails from this domain
        if hist_bias["skip"] >= 0.8:
            return "keep"
        # User has consistently trashed emails from this domain
        if hist_bias["trash"] >= 0.8 and score >= score_threshold * 0.3:
            return "trash"
        # User has consistently marked as promotional
        if hist_bias["promotional"] >= 0.8:
            return "promotional"

    # ── 3. Medium-confidence signals ──
    if llm_label == "spam":
        return "trash"

    # Raise the effective threshold when history leans toward keep
    effective_score_threshold = score_threshold
    if hist_bias and hist_bias["skip"] > 0.5:
        effective_score_threshold = score_threshold * (1.0 + history_weight)

    if score >= effective_score_threshold or action in (
        "add header", "rewrite subject", "soft reject", "quarantine",
    ):
        return "promotional"

    if llm_label in ("promotional", "marketing", "ads"):
        # If moderate history says keep, don't blindly trust LLM
        if hist_bias and hist_bias["skip"] > 0.6:
            return "keep"
        return "promotional"

    # ── 4. Moderate history tiebreakers for borderline cases ──
    if hist_bias:
        if hist_bias["trash"] > 0.6 and score >= score_threshold * 0.5:
            return "trash"
        if hist_bias["promotional"] > 0.6 and score >= score_threshold * 0.5:
            return "promotional"

    # ── 5. Default ──
    return "keep"

def get_action_display(action: str) -> str:
    """Get display name for action"""
    if action == "promotional":
        return "PROMOTIONAL"
    elif action == "trash":
        return "SPAM"
    else:
        return "KEEP"

def prompt_user(subject: str, from_addr: str, rspamd_score: float, llm_label: str, recommended_action: str, domain_history: dict[str, int] | None = None) -> str:
    """Show email info and prompt user for action"""
    print("\n" + "="*80)
    print(f"From: {from_addr}")
    print(f"Subject: {subject}")
    print("-"*80)
    print("Analysis:")
    print(f"  • Rspamd Score: {rspamd_score:.2f}")
    print(f"  • LLM Classification: {llm_label}")

    # Show historical information if available
    if domain_history:
        total = sum(domain_history.values())
        if total >= HISTORY_MIN_SAMPLES:
            trash_pct = int(domain_history.get("trash", 0) / total * 100)
            promo_pct = int(domain_history.get("promotional", 0) / total * 100)
            keep_pct = int(domain_history.get("skip", 0) / total * 100)
            print(f"  • Historical: {total} past email(s) ({trash_pct}% spam, {promo_pct}% promotional, {keep_pct}% keep)")

    print(f"  • Recommended: {get_action_display(recommended_action)}")
    print("-"*80)

    if recommended_action == "promotional":
        default = "p"
        prompt_text = "Action? [P]romotional (default), (s)pam, (k)eep: "
    elif recommended_action == "trash":
        default = "s"
        prompt_text = "Action? [S]pam (default), (p)romotional, (k)eep: "
    else:
        default = "k"
        prompt_text = "Action? [K]eep (default), (p)romotional, (s)pam: "

    while True:
        try:
            response = input(prompt_text).strip().lower()
            if not response:
                response = default

            if response in ('p', 'promotional'):
                return 'promotional'
            elif response in ('s', 'spam'):
                return 'trash'
            elif response in ('k', 'keep', 'skip'):
                return 'skip'
            else:
                print("Invalid choice. Please enter p, s, or k (or just press Enter for default)")
        except (EOFError, KeyboardInterrupt):
            print("\nInterrupted by user")
            sys.exit(0)

def _build_sessions() -> list[tuple[str, Mailbox]]:
    """Build one Mailbox session per enabled provider, or exit on bad config.

    When PROVIDERS is unset, each provider with complete credentials is
    enabled (auto-detect). When PROVIDERS is set, it is the authority: a
    listed provider with missing credentials is a hard error.
    """
    sessions: list[tuple[str, Mailbox]] = []
    if PROVIDERS:
        providers = [p.strip().lower() for p in PROVIDERS.split(",") if p.strip()]
    else:
        providers = []
        if YAHOO_EMAIL and YAHOO_APP_PASSWORD:
            providers.append("yahoo")
        if M365_TENANT_ID and M365_CLIENT_ID and M365_CLIENT_SECRET and M365_MAILBOX:
            providers.append("m365")

    for provider in providers:
        if provider == "yahoo":
            if not (YAHOO_EMAIL and YAHOO_APP_PASSWORD):
                print(
                    "PROVIDERS includes yahoo but YAHOO_EMAIL/YAHOO_APP_PASSWORD missing.",
                    file=sys.stderr,
                )
                sys.exit(1)
            sessions.append(
                (provider, ImapSession(IMAP_HOST, IMAP_PORT, YAHOO_EMAIL, YAHOO_APP_PASSWORD))
            )
        elif provider == "m365":
            if not (M365_TENANT_ID and M365_CLIENT_ID and M365_CLIENT_SECRET and M365_MAILBOX):
                print(
                    "PROVIDERS includes m365 but M365_TENANT_ID/CLIENT_ID/"
                    "CLIENT_SECRET/M365_MAILBOX missing.",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Deferred import: msal/token flow not needed for yahoo-only runs
            from .m365_client import M365Session
            sessions.append((provider, M365Session(M365_MAILBOX or "")))
        else:
            print(f"Unknown provider in PROVIDERS: {provider}", file=sys.stderr)
            sys.exit(1)
    if not sessions:
        print(
            "No mailbox provider configured. Set Yahoo credentials "
            "(YAHOO_EMAIL/YAHOO_APP_PASSWORD) and/or M365 credentials "
            "(M365_TENANT_ID/M365_CLIENT_ID/M365_CLIENT_SECRET/M365_MAILBOX), "
            "or set PROVIDERS explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)
    return sessions


def _prefixed_key(provider: str, uidvalidity: str) -> str:
    """Namespace a uidvalidity by provider to keep both mailboxes apart."""
    return uidvalidity if uidvalidity.startswith(f"{provider}:") else f"{provider}:{uidvalidity}"


def _stable_int(graph_id: str) -> int:
    """Deterministic 31-bit int surrogate for a Graph message id (DB uid col)."""
    import hashlib
    return int.from_bytes(hashlib.sha256(graph_id.encode("utf-8")).digest()[:4], "big") >> 1


def scan_mailbox(
    store: SeenStore,
    session: Mailbox,
    provider: str,
    mailbox: str,
    interactive: bool,
) -> None:
    """Scan one mailbox via a Mailbox session: triage + move + record.

    Yahoo (IMAP) uses numeric UIDs and search_since_uid; M365 (Graph) uses the
    delta-token cursor. Both share the rspamd + LLM decision pipeline here.
    """
    is_graph = hasattr(session, "search_since_cursor")
    session.select_mailbox(mailbox)
    session.ensure_folder(DEST_FOLDER)  # type: ignore[arg-type]
    session.ensure_folder(TRASH_FOLDER)  # type: ignore[arg-type]

    uidvalidity = _prefixed_key(provider, session.get_uidvalidity(mailbox))

    if is_graph:
        if store.get_last_uid(uidvalidity) > 0:
            print("Resuming (progress saved from previous run).")
        messages, next_cursor = session.search_since_cursor(store.get_cursor(uidvalidity))  # type: ignore[attr-defined]
        store.set_cursor(uidvalidity, next_cursor)
        # Skip tombstones; process upserted messages
        ids: list[str | int] = [m["id"] for m in messages if m["change"] != "deleted"]
    else:
        last_uid = store.get_last_uid(uidvalidity)
        if last_uid > 0:
            print(f"Resuming from UID {last_uid} (progress saved from previous run).")
        ids = session.search_since_uid(last_uid)  # type: ignore[assignment]

    if not ids:
        print("No new emails.")
        return

    print(f"[{provider}] Processing {len(ids)} email(s)...")
    if interactive:
        print("Interactive mode enabled. You will be prompted for each email.")
    else:
        print("Auto mode enabled. Applying recommended actions automatically.")

    for mid in ids:
        if is_graph:
            raw = session.fetch_message_rfc822(str(mid))  # type: ignore[attr-defined]
            subject, from_addr = extract_email_info(raw)
            # classify_message wants header text; the raw RFC822 carries them
            hdr = raw.decode("utf-8", errors="replace")
        else:
            raw = session.fetch_rfc822(int(mid))
            subject, from_addr = extract_email_info(raw)
            hdr = session.fetch_headers(int(mid))

        # Extract domain and get historical actions
        domain = extract_domain(from_addr)
        domain_history = store.get_domain_history(domain) if domain else {}

        # Get analysis
        rsp = check_message(RSPAMD_URL, raw)
        llm = classify_message(hdr, raw)
        rspamd_score = rsp.get('score', 0.0)

        # Decide recommended action with history
        recommended = decide_action(
            rsp,
            llm,
            RSPAMD_SPAM_SCORE,
            RSPAMD_TRASH_SCORE,
            domain_history=domain_history,
            history_weight=HISTORY_WEIGHT,
            history_min_samples=HISTORY_MIN_SAMPLES,
        )

        # Interactive mode: ask user
        if interactive:
            final_action = prompt_user(subject, from_addr, rspamd_score, llm, recommended, domain_history)
            mode = "interactive"
        else:
            final_action = recommended
            mode = "auto"
            print(f"\n{subject[:60]}... → {get_action_display(recommended)}", flush=True)

        # Execute action (IMAP takes uids; Graph takes message ids)
        if final_action == "promotional":
            _move(session, mid, DEST_FOLDER)
            print(f"✓ Moved to {DEST_FOLDER}")
        elif final_action == "trash":
            _move(session, mid, TRASH_FOLDER)
            print(f"✓ Moved to {TRASH_FOLDER}")
        else:  # skip/keep
            print("✓ Kept in inbox")

        # Record action to database
        store.record_action(
            uidvalidity=uidvalidity,
            uid=mid if isinstance(mid, int) else _stable_int(str(mid)),
            message_id=None if isinstance(mid, int) else str(mid),
            from_addr=from_addr,
            subject=subject,
            rspamd_score=rspamd_score,
            llm_label=llm,
            recommended_action=recommended,
            final_action=final_action,
            mode=mode,
        )

        # Yahoo advances the UID cursor per message; M365 cursor already saved
        if not is_graph:
            store.set_last_uid(uidvalidity, int(mid))

    print(f"[{provider}] Done! Processed {len(ids)} email(s).")


def _move(session: Mailbox, mid: "str | int", dest: str) -> None:
    """Move via the provider's native id type (IMAP uid vs Graph message id)."""
    if hasattr(session, "move_message"):
        session.move_message(str(mid), dest)  # type: ignore[attr-defined]
    else:
        session.move_to_folder(int(mid), dest)


def main() -> None:
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Inbox cleaner (Yahoo IMAP + M365 Graph) using Rspamd + LLM classification"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Automatically apply recommended actions without prompting (overrides INTERACTIVE=true)"
    )
    args = parser.parse_args()

    # Determine if interactive mode is enabled
    interactive = INTERACTIVE and not args.auto

    store = SeenStore(SQLITE_PATH)
    sessions = _build_sessions()

    for provider, session in sessions:
        print(f"\n=== {provider.upper()} ===")
        with session:
            mailbox = M365_MAILBOX_FOLDER if provider == "m365" else MAILBOX
            scan_mailbox(store, session, provider, mailbox, interactive)

if __name__ == "__main__":
    main()
