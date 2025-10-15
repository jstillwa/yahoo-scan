# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Yahoo inbox cleaner that combines Rspamd spam detection with LLM classification (OpenRouter) to automatically organize Yahoo Mail inbox. The system learns from user actions over time to improve recommendations.

## Development Commands

### Setup
```bash
# Native installation with uv
uv venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv sync --no-dev
uv pip install -e .

# Set up OpenRouter API key
llm keys set openrouter
# Or: export OPENROUTER_KEY=sk-or-...

# Configure .env file
cp .env.example .env
# Edit .env with your Yahoo credentials and settings
```

### Running
```bash
# Interactive mode (default)
uv run inbox-cleaner

# Automatic mode (no prompts)
uv run inbox-cleaner --auto

# Docker with rspamd
docker compose up -d rspamd
docker compose run --rm cleaner
```

### Database Operations
```bash
# Reset last UID to reprocess all emails
sqlite3 ./data/state.sqlite "UPDATE progress SET last_uid = 0"

# View recent actions
sqlite3 ./data/state.sqlite "SELECT datetime(processed_at), from_addr, subject, final_action FROM email_actions ORDER BY processed_at DESC LIMIT 10"

# View domain history
sqlite3 ./data/state.sqlite "SELECT final_action, COUNT(*) FROM email_actions WHERE from_addr LIKE '%@amazon.com%' GROUP BY final_action"
```

## Architecture

### Core Pipeline (cli.py main loop)
1. **IMAP Connection** → Fetch new emails via `ImapSession`
2. **Spam Detection** → Send to Rspamd via `check_message()`
3. **LLM Classification** → Send to OpenRouter via `classify_message()`
4. **Historical Learning** → Query past actions from SQLite via `get_domain_history()`
5. **Decision Logic** → `decide_action()` combines all signals with weighted history
6. **User Interaction** → Interactive prompt or auto-apply in `--auto` mode
7. **Action Execution** → Move to folder via IMAP or keep in inbox
8. **Audit Trail** → Record to SQLite via `record_action()`

### Decision Logic Flow (cli.py:85-136)

The `decide_action()` function applies signals in priority order:

1. **High-confidence spam** (score ≥ RSPAMD_TRASH_SCORE or action="reject") → trash (history doesn't override)
2. **LLM says spam** → trash (history doesn't override)
3. **Historical learning for borderline cases**:
   - If domain history shows >60% trash AND score ≥ 50% threshold → trash
   - If domain history shows >60% promotional AND score is borderline → promotional
4. **Medium spam score** (≥ RSPAMD_SPAM_SCORE) → promotional
5. **LLM says promotional/marketing/ads** → promotional
6. **Historical learning for keep decisions**:
   - If domain history shows >70% keep AND score is low → keep
   - If domain history shows >50% promotional AND score ≥ 80% threshold → promotional
7. **Default** → keep

### Historical Learning System

**Domain Extraction** (cli.py:60-67): Extracts domain from email addresses using regex (e.g., "amazon.com" from "no-reply@amazon.com")

**History Calculation** (cli.py:69-83): `calculate_historical_bias()` computes action percentages when ≥ HISTORY_MIN_SAMPLES past emails exist from the domain

**Weighting**: HISTORY_WEIGHT (default 0.3) controls influence strength. History acts as a "learned preference" for borderline cases, not a hard override.

**Database Query** (db.py:95-114): `get_domain_history()` uses LIKE pattern matching on from_addr with index for performance

### Module Responsibilities

**cli.py**: Main orchestration, decision logic, interactive prompts, configuration loading
**db.py**: SQLite operations for progress tracking (last UID) and action history (email_actions table)
**imap_client.py**: Yahoo IMAP operations with context manager pattern. Key: `_quote_folder()` wraps folder names with spaces in quotes for IMAP protocol. `BODY.PEEK[]` preserves read/unread status.
**rspamd.py**: HTTP POST to Rspamd /checkv2 endpoint, returns spam score and action
**classify.py**: Uses `llm` package (via llm-openrouter plugin) to classify emails as spam/promotional/normal

### Key Implementation Details

**IMAP Folder Quoting**: Yahoo's "Bulk Mail" spam folder contains a space. The `_quote_folder()` method wraps such names in double quotes for IMAP commands (CREATE, COPY).

**Read/Unread Preservation**: `fetch_rfc822()` uses `BODY.PEEK[]` instead of `RFC822` to avoid marking messages as read. IMAP COPY preserves all flags including `\Seen`.

**Email Header Decoding**: `decode_email_header()` handles "unknown-8bit" and invalid encodings gracefully with fallback to UTF-8.

**LLM Integration**: Uses Simon Willison's `llm` package with openrouter/ prefix (e.g., `openrouter/google/gemini-2.5-flash`). API key via `llm keys set openrouter` or OPENROUTER_KEY env var.

**Progress Tracking**: SQLite stores UIDVALIDITY + last_uid to handle mailbox changes. Only processes UIDs > last_uid.

**Action Recording**: email_actions table has UNIQUE(uidvalidity, uid) constraint. Reprocessing updates existing records via ON CONFLICT.

## Configuration Tuning

**HISTORY_WEIGHT**: 0.0 = disabled, 0.3 = moderate (default), 1.0 = strong influence
**HISTORY_MIN_SAMPLES**: Minimum past emails from domain before using history (default: 3)
**RSPAMD_SPAM_SCORE**: Threshold for promotional folder (default: 6.0)
**RSPAMD_TRASH_SCORE**: Threshold for spam folder (default: 7.0)

Lower HISTORY_MIN_SAMPLES to 1-2 for faster learning on personal accounts. Raise RSPAMD_SPAM_SCORE if too many emails are marked promotional.

## Type Safety Requirements

This codebase follows strict typing standards:

- **No `Any`**: All functions fully annotated with precise types
- Use `typing` and `typing_extensions` for Literal, Protocol, TypedDict
- Annotate `__init__` with `-> None`
- Prefer `list[str]` over `List[str]`
- Use Protocol for I/O boundaries
- Mark overrides with `@override`
- No broad `except:` - catch concrete exceptions

When adding features, maintain these standards. If third-party libs lack types, add typed Protocol facades.

## License

Apache 2.0 - Copyright 2025 John Stillwagen
