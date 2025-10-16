# Inbox Cleaner

Single-user Yahoo IMAP inbox triage using Rspamd + LLM.

## Overview

This tool automatically processes your Yahoo inbox to identify and move spam/promotional emails to a designated folder. It combines:

- **Rspamd**: Local spam detection engine with scoring
- **OpenRouter LLM**: AI classification using Gemini 2.5 Flash via OpenRouter
- **SQLite**: Progress tracking to avoid reprocessing emails

## Features

- **Interactive mode**: Review each email with AI recommendations and choose action (promotional/spam/keep)
- **Historical learning**: Learns from your past actions to improve recommendations over time
- Zero-framework Python CLI using `uv` for fast, reproducible installs
- Processes only new emails since last run (tracks UIDVALIDITY)
- Combines Rspamd spam scores with LLM classification
- Moves spam/promotional emails to folders
- Preserves read/unread status during processing
- Complete audit log of all processed emails
- Cross-platform: Docker or native on Windows/macOS/Linux

## Prerequisites

### Yahoo Mail Setup

1. Yahoo discontinued basic password authentication in 2024
2. You need to create an **App Password**:
   - Go to Yahoo Account Security settings
   - Generate a new app password for "Mail"
   - Use this password instead of your regular password

### LLM Setup

This tool uses the [llm package](https://llm.datasette.io) which supports multiple providers.

**Option 1: OpenRouter (Recommended)**
1. Sign up at [openrouter.ai](https://openrouter.ai)
2. Create an API key
3. Add credits to your account (GPT-4o-mini is very cheap - ~$0.0001 per email)
4. Set your API key:
   ```bash
   llm keys set openrouter
   # Paste your API key when prompted
   ```

**Option 2: Use environment variable**
Add to your `.env` file:
```bash
OPENROUTER_KEY=sk-or-your-key-here
```

## Installation

### Option 1: Docker (Recommended)

1. Clone this repository
2. Copy the environment template:
   ```bash
   cp .env.example .env
   ```

3. Edit `.env` with your credentials (just the required fields):
   ```bash
   YAHOO_EMAIL=yourname@yahoo.com
   YAHOO_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
   # Note: Set OpenRouter key using: llm keys set openrouter
   ```

   Note: RSPAMD_URL and SQLITE_PATH are automatically configured for Docker in docker-compose.yml

4. Start the services:
   ```bash
   docker compose up --build cleaner
   ```

### Option 2: Native Installation with uv

1. Install `uv`:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. Create and activate a virtual environment:
   ```bash
   uv venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   uv sync --no-dev
   uv pip install -e .
   ```

4. Create and configure `.env` file:
   ```bash
   cp .env.example .env
   ```

   Edit `.env` with your credentials (just the required fields).

5. Start Rspamd (if not already running):
   ```bash
   docker compose up -d rspamd
   ```

6. Run the cleaner:

   **Option A: With activated venv**
   ```bash
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   inbox-cleaner
   ```

   **Option B: Using uv run (no activation needed)**
   ```bash
   uv run inbox-cleaner
   ```

   The app automatically loads variables from the `.env` file.

7. **Optional: Run in automatic mode**

   To automatically apply all recommendations without prompting:
   ```bash
   inbox-cleaner --auto
   # or with uv:
   uv run inbox-cleaner --auto
   ```

   This overrides `INTERACTIVE=true` and applies all recommended actions automatically.

## Configuration

All configuration is done via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `YAHOO_EMAIL` | (required) | Your Yahoo email address |
| `YAHOO_APP_PASSWORD` | (required) | Yahoo app password |
| `OPENROUTER_KEY` | (required*) | OpenRouter API key (set via `llm keys set openrouter`) |
| `LLM_MODEL` | `openrouter/google/gemini-2.5-flash` | LLM model to use (any OpenRouter model) |
| `LLM_MAX_CHARS` | `2000000` | Max characters to send to LLM (~500K tokens, Gemini supports 1M) |
| `IMAP_HOST` | `imap.mail.yahoo.com` | Yahoo IMAP server |
| `IMAP_PORT` | `993` | IMAP SSL port |
| `MAILBOX` | `INBOX` | Mailbox to scan |
| `DEST_FOLDER` | `Promotional` | Folder for promotional emails |
| `TRASH_FOLDER` | `Bulk Mail` | Folder for spam emails (Yahoo's spam folder) |
| `INTERACTIVE` | `true` | Enable interactive confirmation mode |
| `SQLITE_PATH` | `./state.sqlite` | SQLite database path |
| `RSPAMD_URL` | `http://127.0.0.1:11333/checkv2` | Rspamd API endpoint |
| `RSPAMD_SPAM_SCORE` | `6.0` | Score threshold for promotional folder |
| `RSPAMD_TRASH_SCORE` | `7.0` | Score threshold for spam folder |
| `HISTORY_WEIGHT` | `0.3` | Historical learning influence (0.0-1.0) |
| `HISTORY_MIN_SAMPLES` | `3` | Minimum past emails before using history |

## Interactive Mode

By default, the cleaner runs in **interactive mode**, showing you each email with:

- **Email details**: From address and subject line
- **AI Analysis**: Rspamd spam score and LLM classification
- **Recommended action**: What the AI thinks should be done

For each email, you can:
- Press **Enter** to accept the recommended action (default)
- Press **p** to move to Promotional folder
- Press **s** to move to Spam folder
- Press **k** to skip (keep in inbox)

Example output:
```
================================================================================
From: newsletter@example.com
Subject: Weekly Deals - 50% Off!
--------------------------------------------------------------------------------
Analysis:
  • Rspamd Score: 7.32
  • LLM Classification: promotional
  • Historical: 12 past email(s) (8% spam, 75% promotional, 17% keep)
  • Recommended: PROMOTIONAL
--------------------------------------------------------------------------------
Action? [P]romotional (default), (s)pam, (k)eep:
```

To run in **automatic mode** (no prompts):
- Use the `--auto` flag: `inbox-cleaner --auto`
- Or set `INTERACTIVE=false` in your `.env` file

The `--auto` flag is useful for one-time automatic runs while keeping interactive mode as the default.

## Historical Learning

The cleaner learns from your past actions to improve future recommendations. When processing an email, it looks up previous actions you've taken on emails from the same domain.

**How it works:**

1. **Domain extraction**: Extracts domain from sender (e.g., "amazon.com" from "no-reply@amazon.com")
2. **History lookup**: Queries database for all past actions on emails from this domain
3. **Pattern detection**: If ≥3 past emails exist, calculates percentages for each action
4. **Weighted influence**: Applies historical patterns as a "bump" to the recommendation

**Example scenarios:**

- **Known spam domain**: If you've marked 8/8 emails from "sketchy-deals.com" as spam, future emails from that domain will be strongly biased toward spam
- **Amazon promotional**: If you've marked 12/15 Amazon emails as promotional, future Amazon emails will lean toward promotional when signals are borderline
- **Personal contacts**: If you've kept 5/5 emails from "john@company.com", future emails will be more likely to stay in inbox

**Configuration:**

- `HISTORY_WEIGHT` (default: 0.3): Controls influence strength
  - `0.0` = disabled (no historical learning)
  - `0.3` = moderate influence (recommended)
  - `1.0` = strong influence
- `HISTORY_MIN_SAMPLES` (default: 3): Minimum past emails needed before using history

**Important notes:**

- History acts as a "learned preference" for borderline cases
- Strong signals (high spam scores, explicit LLM classifications) still take precedence
- History is applied non-deterministically to avoid false positives
- Interactive mode shows historical percentages in the prompt

## How It Works

1. **Connect to IMAP**: Logs into Yahoo Mail using app password
2. **Check for new emails**: Uses SQLite to track last processed UID
3. **Spam detection**: Sends each email to Rspamd for scoring
4. **LLM classification**: Sends headers/body to OpenRouter for categorization
5. **Decision logic**:
   - If LLM classifies as "spam" → recommend **SPAM** (move to Bulk Mail)
   - If Rspamd score >= trash threshold (7.0) → recommend **SPAM** (move to Bulk Mail)
   - If Rspamd score >= spam threshold (6.0) → recommend **PROMOTIONAL**
   - If LLM classifies as "promotional/marketing/ads" → recommend **PROMOTIONAL**
   - Otherwise → recommend **KEEP** in inbox
6. **Move emails**: Copies to destination folder and deletes from inbox
7. **Save progress**: Updates SQLite with last processed UID

## Command-Line Options

```
usage: inbox-cleaner [-h] [--auto]

Yahoo inbox cleaner using Rspamd + LLM classification

options:
  -h, --help  show this help message and exit
  --auto      Automatically apply recommended actions without prompting (overrides INTERACTIVE=true)
```

## Scheduling

### GitHub Actions (Recommended for Cloud Deployment)

Deploy to run automatically every hour using GitHub Actions (completely free):

**Setup Steps:**

1. **Fork/push this repository to GitHub**

2. **Add repository secrets** (Settings → Secrets and variables → Actions):
   - `YAHOO_EMAIL` - Your Yahoo email address
   - `YAHOO_PASSWORD` - Your Yahoo app password
   - `OPENROUTER_KEY` - Your OpenRouter API key

   **Optional: Customize other settings**

   By default, the workflow uses these settings (defined in `.github/workflows/clean-inbox.yml`):
   - LLM Model: `openrouter/google/gemini-2.0-flash-exp:free`
   - Spam score threshold: `6.0`
   - Trash score threshold: `7.0`
   - History weight: `0.3`
   - History min samples: `3`

   To customize these, edit the `.env` file creation section in the workflow file

3. **Enable GitHub Actions** in your repository settings

4. **The workflow runs automatically every hour**
   - Workflow file: `.github/workflows/clean-inbox.yml`
   - Runs at minute 0 of every hour
   - Uses Docker Compose (rspamd + cleaner)
   - Runs in `--auto` mode (no prompts)
   - SQLite database persists between runs via GitHub artifacts

**Manual Trigger:**

You can also trigger the workflow manually from the Actions tab:
- Go to Actions → Clean Yahoo Inbox → Run workflow

**Monitoring:**

View execution logs in the Actions tab to see:
- How many emails were processed
- Which actions were taken
- Any errors or issues

**Notes:**
- Free tier includes 2,000 minutes/month (plenty for hourly runs)
- Database state is preserved between runs for 90 days
- Secrets are encrypted and never exposed in logs
- All sensitive data stays in repository secrets

### Local Scheduling

Run periodically using your system's scheduler:

#### Linux/macOS (cron)

```bash
# Run every 15 minutes in auto mode
*/15 * * * * cd /path/to/inbox-cleaner && docker compose run --rm cleaner --auto

# Or for native installation:
*/15 * * * * cd /path/to/inbox-cleaner && /path/to/.venv/bin/inbox-cleaner --auto
```

#### Windows (Task Scheduler)

1. Open Task Scheduler
2. Create Basic Task
3. Trigger: Daily, repeat every 15 minutes
4. Action: Start a program
5. Program: `docker`
6. Arguments: `compose run --rm cleaner --auto`
7. Start in: `C:\path\to\inbox-cleaner`

**Note:** The `--auto` flag ensures scheduled runs execute automatically without waiting for user input.

## Email Processing History

The tool maintains a complete audit log of all processed emails in the SQLite database:

**Tracked Information:**
- Email metadata (from, subject)
- Rspamd spam score
- LLM classification label
- Recommended action (what the AI suggested)
- Final action taken (what actually happened)
- Processing mode (auto or interactive)
- Timestamp of processing

**Querying the History:**

```bash
# View recent actions
sqlite3 ./data/state.sqlite "SELECT datetime(processed_at), from_addr, subject, final_action, mode FROM email_actions ORDER BY processed_at DESC LIMIT 10"

# Count actions by type
sqlite3 ./data/state.sqlite "SELECT final_action, COUNT(*) FROM email_actions GROUP BY final_action"

# View emails where user overrode recommendation
sqlite3 ./data/state.sqlite "SELECT from_addr, subject, recommended_action, final_action FROM email_actions WHERE recommended_action != final_action"

# View all spam detections
sqlite3 ./data/state.sqlite "SELECT datetime(processed_at), from_addr, subject, rspamd_score FROM email_actions WHERE final_action = 'trash' ORDER BY processed_at DESC"

# View history for a specific domain
sqlite3 ./data/state.sqlite "SELECT final_action, COUNT(*) FROM email_actions WHERE from_addr LIKE '%@amazon.com%' GROUP BY final_action"
```

## Notes

- Yahoo does not provide a default "Promotional" folder; the app creates it automatically
- SQLite stores both progress tracking and complete email processing history
- Email read/unread status is preserved during processing
- If Rspamd is unavailable, the app will fail (ensure rspamd service is running)
- LLM classification uses OpenRouter API with minimal prompts to keep costs low
- The tool uses COPY + DELETE instead of MOVE for broader IMAP compatibility

## Architecture

```
inbox-cleaner/
├── pyproject.toml          # uv configuration
├── inbox_cleaner/
│   ├── __init__.py
│   ├── cli.py              # Main CLI entrypoint
│   ├── db.py               # SQLite progress tracking
│   ├── imap_client.py      # Yahoo IMAP client
│   ├── rspamd.py           # Rspamd HTTP API
│   └── classify.py         # OpenRouter LLM classification
├── Dockerfile              # Container image with uv
├── docker-compose.yml      # Rspamd + cleaner services
├── .env.example            # Configuration template
└── README.md
```

## Troubleshooting

### "Authentication failed"
- Verify you're using an App Password, not your regular password
- Check that the email address is correct

### "Connection refused" to Rspamd
- Ensure rspamd service is running: `docker compose up -d rspamd`
- Wait a few seconds for rspamd to start

### "No new emails" but I have unprocessed emails
- Delete `state.sqlite` to reset progress tracking
- The tool only processes emails with UID > last processed UID

### LLM classification errors

- Set your OpenRouter API key: `llm keys set openrouter`
- Or add to `.env`: `OPENROUTER_KEY=sk-or-your-key`
- Check you have credits in your OpenRouter account
- Ensure the LLM_MODEL uses the `openrouter/` prefix (e.g., `openrouter/google/gemini-2.5-flash`)
- List available models: `llm models list`

### GitHub Actions deployment issues

**Workflow not running:**
- Check that Actions are enabled in repository Settings → Actions → General
- Verify the workflow file is at `.github/workflows/clean-inbox.yml`
- Check the Actions tab for error messages

**Authentication errors:**
- Verify all three secrets are set: `YAHOO_EMAIL`, `YAHOO_PASSWORD`, `OPENROUTER_KEY`
- Use Yahoo app password, not regular password
- Secret names must match exactly (case-sensitive)

**Database not persisting:**
- Check Actions tab → workflow run → Artifacts section
- Artifact named "inbox-cleaner-state" should be uploaded after each run
- First run won't have an artifact (this is normal)

**Manually updating the database artifact:**

If you need to modify the database (reset progress, clear history, merge local changes, etc.):

1. **Download the current artifact:**
   ```bash
   gh run download --name inbox-cleaner-state
   # This downloads state.sqlite to your current directory
   ```

2. **Modify the database:**
   ```bash
   # Reset last UID to reprocess all emails
   sqlite3 state.sqlite "UPDATE progress SET last_uid = 0"

   # Clear all history
   sqlite3 state.sqlite "DELETE FROM email_actions"

   # Interactive SQL session for custom queries
   sqlite3 state.sqlite

   # Or merge with local database
   mkdir -p ./data
   sqlite3 state.sqlite << 'EOF'
   ATTACH DATABASE './data/state.sqlite' AS local;
   INSERT OR IGNORE INTO email_actions
     SELECT * FROM local.email_actions;
   DETACH DATABASE local;
   EOF
   ```

3. **Upload the modified database using the upload workflow:**
   ```bash
   # Copy modified database to data directory
   mkdir -p ./data
   cp state.sqlite ./data/state.sqlite

   # Commit temporarily (data/ is in .gitignore, so use -f)
   git add -f ./data/state.sqlite
   git commit -m "temp: database for upload"
   git push

   # Trigger the upload workflow
   gh workflow run upload-db.yml

   # Wait for completion, then clean up
   sleep 15  # wait for upload to complete
   git rm data/state.sqlite
   git commit -m "cleanup: remove temp database"
   git push
   ```

   The `upload-db.yml` workflow uploads `./data/state.sqlite` as the `inbox-cleaner-state` artifact, which the main `clean-inbox.yml` workflow will use on the next run.

**Common database operations:**

```bash
# View recent actions
sqlite3 state.sqlite "SELECT datetime(processed_at), from_addr, subject, final_action FROM email_actions ORDER BY processed_at DESC LIMIT 10"

# Check current progress
sqlite3 state.sqlite "SELECT * FROM progress"

# View domain history
sqlite3 state.sqlite "SELECT final_action, COUNT(*) FROM email_actions WHERE from_addr LIKE '%@amazon.com%' GROUP BY final_action"
```

**Rspamd container issues:**
- Check workflow logs for "Rspamd is ready!" message
- If timeout occurs, rspamd may need more startup time
- View rspamd logs in the workflow output under "Show logs on failure"

## License

Copyright 2025 John Stillwagen

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this software except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
