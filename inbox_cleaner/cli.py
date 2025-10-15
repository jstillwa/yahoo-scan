#!/usr/bin/env python3

import os
import sys
import argparse
from dotenv import load_dotenv
from .imap_client import ImapSession
from .db import SeenStore
from .rspamd import check_message
from .classify import classify_message
from email import message_from_bytes
from email.header import decode_header

# Load .env file from current directory or parent directories
load_dotenv()

IMAP_HOST = os.getenv("IMAP_HOST", "imap.mail.yahoo.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
YAHOO_EMAIL = os.getenv("YAHOO_EMAIL")
YAHOO_APP_PASSWORD = os.getenv("YAHOO_APP_PASSWORD")
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

def decode_email_header(header_value):
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

def extract_email_info(raw_email: bytes):
    """Extract subject and from fields from raw email"""
    msg = message_from_bytes(raw_email)
    subject = decode_email_header(msg.get('Subject', '(No Subject)'))
    from_addr = decode_email_header(msg.get('From', '(Unknown)'))
    return subject, from_addr

def extract_domain(from_addr: str) -> str:
    """Extract domain from email address (e.g., 'Name <user@example.com>' -> 'example.com')"""
    import re
    # Handle formats: "Name <email@domain.com>" or "email@domain.com"
    match = re.search(r'[\w\.-]+@([\w\.-]+)', from_addr)
    if match:
        return match.group(1).lower()
    return ""

def calculate_historical_bias(domain_history: dict, min_samples: int = 3):
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
    rspamd_result: dict,
    llm_label: str,
    score_threshold: float,
    spam_threshold: float = 10.0,
    domain_history: dict = None,
    history_weight: float = 0.3,
    history_min_samples: int = 3,
):
    """Decide what action to take based on rspamd, LLM, and historical results"""
    score = rspamd_result.get("score", 0.0)
    action = (rspamd_result.get("action") or "").lower()

    # Calculate historical bias
    hist_bias = calculate_historical_bias(domain_history, history_min_samples)

    # High confidence spam → trash (history doesn't override this)
    if score >= spam_threshold or action in ("reject",):
        return "trash"

    # LLM says spam → trash (history doesn't override this)
    if llm_label == "spam":
        return "trash"

    # Apply historical learning for borderline cases
    if hist_bias:
        # If history strongly suggests trash (>60%) and score is above half threshold
        if hist_bias["trash"] > 0.6 and score >= score_threshold * 0.5:
            return "trash"

        # If history strongly suggests promotional (>60%) and score is borderline
        if hist_bias["promotional"] > 0.6 and score >= score_threshold * 0.7:
            return "promotional"

    # Medium spam score or promotional content → promotional folder
    if score >= score_threshold or action in ("add header", "rewrite subject", "soft reject", "quarantine"):
        return "promotional"

    if llm_label in ("promotional", "marketing", "ads"):
        return "promotional"

    # Apply historical learning for keep vs promotional decision
    if hist_bias:
        # If history strongly suggests keep (>70%), raise threshold for promotional
        if hist_bias["skip"] > 0.7 and score < score_threshold * 0.9:
            return "keep"

        # If history suggests promotional (>50%), lower threshold slightly
        if hist_bias["promotional"] > 0.5 and score >= score_threshold * 0.8:
            return "promotional"

    return "keep"

def get_action_display(action: str) -> str:
    """Get display name for action"""
    if action == "promotional":
        return "PROMOTIONAL"
    elif action == "trash":
        return "SPAM"
    else:
        return "KEEP"

def prompt_user(subject: str, from_addr: str, rspamd_score: float, llm_label: str, recommended_action: str, domain_history: dict = None):
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

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Yahoo inbox cleaner using Rspamd + LLM classification"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Automatically apply recommended actions without prompting (overrides INTERACTIVE=true)"
    )
    args = parser.parse_args()

    # Determine if interactive mode is enabled
    interactive = INTERACTIVE and not args.auto

    if not (YAHOO_EMAIL and YAHOO_APP_PASSWORD):
        print("Missing YAHOO_EMAIL or YAHOO_APP_PASSWORD env vars.", file=sys.stderr)
        sys.exit(1)

    store = SeenStore(SQLITE_PATH)
    with ImapSession(IMAP_HOST, IMAP_PORT, YAHOO_EMAIL, YAHOO_APP_PASSWORD) as imap:
        imap.select_mailbox(MAILBOX)
        imap.ensure_folder(DEST_FOLDER)
        imap.ensure_folder(TRASH_FOLDER)

        uidvalidity = imap.get_uidvalidity(MAILBOX)
        last_uid = store.get_last_uid(uidvalidity)

        uids = imap.search_since_uid(last_uid)
        if not uids:
            print("No new emails.")
            return

        print(f"Processing {len(uids)} email(s)...")
        if interactive:
            print("Interactive mode enabled. You will be prompted for each email.")
        else:
            print("Auto mode enabled. Applying recommended actions automatically.")

        for uid in uids:
            raw = imap.fetch_rfc822(uid)
            hdr = imap.fetch_headers(uid)

            # Extract email info for display
            subject, from_addr = extract_email_info(raw)

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
                # Auto mode: use recommended action and show what we're doing
                final_action = recommended
                mode = "auto"
                print(f"\n{subject[:60]}... → {get_action_display(recommended)}", flush=True)

            # Execute action
            if final_action == "promotional":
                imap.move_to_folder(uid, DEST_FOLDER)
                print(f"✓ Moved to {DEST_FOLDER}")
            elif final_action == "trash":
                imap.move_to_folder(uid, TRASH_FOLDER)
                print(f"✓ Moved to {TRASH_FOLDER}")
            else:  # skip/keep
                print("✓ Kept in inbox")

            # Record action to database
            store.record_action(
                uidvalidity=uidvalidity,
                uid=uid,
                from_addr=from_addr,
                subject=subject,
                rspamd_score=rspamd_score,
                llm_label=llm,
                recommended_action=recommended,
                final_action=final_action,
                mode=mode,
            )

            # Always update progress
            store.set_last_uid(uidvalidity, uid)

        print(f"\nDone! Processed {len(uids)} email(s).")

if __name__ == "__main__":
    main()
