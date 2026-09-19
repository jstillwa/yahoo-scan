# Inbox Cleaner

Single-user inbox triage tool. It scans Yahoo Mail (IMAP) and Microsoft 365
(Graph) with Rspamd and LLM classification.

## Overview

The tool reads each mailbox, classifies each new email, and moves spam and
promotional email to target folders. Each provider is optional: the tool
enables every provider with complete credentials, so Yahoo-only, M365-only,
and both-in-one-run all work without extra configuration. Set `PROVIDERS`
explicitly to pin the list. It uses:

- **Rspamd**: local spam scoring
- **OpenRouter LLM**: email classification (Gemini 2.5 Flash via OpenRouter)
- **SQLite**: progress tracking. One state file serves both providers. Yahoo
  uses UID cursors. M365 uses Graph delta tokens.

## Features

- **Interactive mode**: review each email with recommendations, then choose
  an action (promotional/spam/keep)
- **Historical learning**: past actions tune later recommendations
- Zero-framework Python CLI, installed with `uv`
- Processes only new emails since the last run
- Combines Rspamd spam scores with LLM classification
- Moves spam and promotional email to folders
- Preserves read/unread status during processing
- Complete audit log of all processed emails
- Cross-platform: Docker, or native on Windows, macOS, and Linux

## Prerequisites

### Yahoo Mail Setup

1. Yahoo discontinued basic password authentication in 2024
2. You need to create an **App Password**:
   - Go to Yahoo Account Security settings
   - Generate a new app password for "Mail"
   - Use this password instead of your regular password

### Microsoft 365 Setup (optional)

M365 support scans a mailbox through Microsoft Graph with app-only (client
credentials) auth. This is an unattended daemon login. Microsoft does not
support app-only IMAP, so Graph is the M365 transport.

1. In [Azure Portal → Microsoft Entra ID → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
   click **New registration**
2. Name it (for example, "inbox-cleaner"). Leave the account type as
   **Accounts in this organizational directory only**. No redirect URI is
   needed.
3. After creation, note the **Application (client) ID** and the
   **Directory (tenant) ID**. These go in `.env` as `M365_CLIENT_ID` and
   `M365_TENANT_ID`.
4. Go to **Certificates & secrets**, then **New client secret**. Copy the
   secret **Value** (not the Secret ID). This becomes `M365_CLIENT_SECRET`.
5. Go to **API permissions**, then **Add a permission**, then **Microsoft
   Graph**, then **Application permissions**. Add:
   - `Mail.Read`
   - `Mail.ReadWrite`
6. Click **Grant admin consent for <tenant>**. An Entra admin must do this.
   Without consent, Graph returns 403.
7. Set the target mailbox in `M365_MAILBOX` (the user's UPN). Optionally set
   `M365_MAILBOX_FOLDER` (default `INBOX`).
8. Add the M365 variables to `.env`:

   ```bash
   PROVIDERS=yahoo,m365
   M365_TENANT_ID=your-tenant-guid
   M365_CLIENT_ID=your-app-client-id
   M365_CLIENT_SECRET=your-secret-value
   M365_MAILBOX=user@yourtenant.com
   ```

Note: you can limit app-only Graph access to specific mailboxes. Use an
**application access policy** in Exchange Online (see the
`ApplicationAccessPolicy` cmdlets). Do this when the app could otherwise
read every mailbox in the tenant.

### LLM Setup

This tool uses the [llm package](https://llm.datasette.io) which supports multiple providers.

**Option 1: OpenRouter (Recommended)**

1. Sign up at [openrouter.ai](https://openrouter.ai)
2. Create an API key
3. Add credits to your account (about $0.0001 per email)
4. Set your API key:

   ```bash
   llm keys set openrouter
   # Paste your API key when prompted
   ```

Or use an environment variable. Add this line to your `.env` file:

```bash
OPENROUTER_KEY=sk-or-your-key-here
```

**Option 2: Ollama**

1. Set `LLM_MODEL` to your preferred Ollama model. Example:
   `LLM_MODEL=llama3.2`
2. If your Ollama server runs on another host, set `OLLAMA_API_BASE`.
   Example: `OLLAMA_API_BASE=http://192.168.1.1:11434`


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

   To also scan a Microsoft 365 mailbox, add the `M365_*` variables from
   [Microsoft 365 Setup](#microsoft-365-setup-optional). No other change is
   needed; the tool enables each provider with complete credentials.

   Note: Docker sets `RSPAMD_URL` and `SQLITE_PATH` automatically in
   `docker-compose.yml`.

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

7. **Optional: run in automatic mode**

   To apply all recommendations without prompts, run:

   ```bash
   inbox-cleaner --auto
   # or with uv:
   uv run inbox-cleaner --auto
   ```

   The `--auto` flag overrides `INTERACTIVE=true` for that run. Interactive
   mode stays the default.

## Configuration

All configuration is done via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROVIDERS` | (auto) | Providers to scan per run: `yahoo`, `m365`, or `yahoo,m365`. Unset = enable every provider with complete credentials. |
| `YAHOO_EMAIL` | (required for yahoo) | Your Yahoo email address |
| `YAHOO_APP_PASSWORD` | (required for yahoo) | Yahoo app password |
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
| `M365_TENANT_ID` | (required for m365) | Entra tenant (directory) ID |
| `M365_CLIENT_ID` | (required for m365) | App registration client ID |
| `M365_CLIENT_SECRET` | (required for m365) | App registration client secret value |
| `M365_MAILBOX` | (required for m365) | Target mailbox UPN (user@tenant.com) |
| `M365_MAILBOX_FOLDER` | `INBOX` | Folder inside the M365 mailbox to scan |

## Interactive Mode

By default, the cleaner runs in **interactive mode**. It shows you each email
with:

- **Email details**: sender address and subject line
- **Analysis**: Rspamd spam score and LLM classification
- **Recommended action**: the suggested classification

For each email, you can:

- Press **Enter** to accept the recommended action (default)
- Press **p** to move to the Promotional folder
- Press **s** to move to the Spam folder
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

## Historical Learning

The cleaner learns from your past actions. For each email, it looks up past
actions on emails from the same sender domain.

**How it works:**

1. **Domain extraction**: extracts the domain from the sender. Example:
   `amazon.com` from `<no-reply@amazon.com>`
2. **History lookup**: queries the database for past actions from that domain
3. **Pattern detection**: with 3 or more past emails, calculates the
   percentage for each action
4. **Weighted influence**: applies the pattern as an adjustment to the
   recommendation

**Example scenarios:**

- **Known spam domain**: you marked 8 of 8 emails from `sketchy-deals.com`
  as spam. Future email from that domain leans toward spam.
- **Amazon promotional**: you marked 12 of 15 Amazon emails as promotional.
  Future Amazon email leans toward promotional when signals are borderline.
- **Personal contacts**: you kept 5 of 5 emails from `<john@company.com>`.
  Future email from that address tends to stay in the inbox.

**Configuration:**

- `HISTORY_WEIGHT` (default: `0.3`): influence strength
  - `0.0` = disabled (no historical learning)
  - `0.3` = moderate influence (recommended)
  - `1.0` = strong influence
- `HISTORY_MIN_SAMPLES` (default: `3`): minimum past emails before the
  tool uses history

**Important notes:**

- History acts as a learned preference for borderline cases
- Strong signals (high spam scores, explicit LLM classifications) take
  precedence
- Interactive mode shows the historical percentages in the prompt

## How It Works

1. **Connect**: Logs into Yahoo via IMAP app password and/or M365 via Graph client-credentials (MSAL)
2. **Check for new emails**: Yahoo tracks the last processed UID in SQLite. M365 uses a Graph delta token, a per-folder change feed
3. **Spam detection**: Sends each email to Rspamd for scoring
4. **LLM classification**: Sends headers/body to OpenRouter for categorization
5. **Decision logic**:
   - If LLM classifies as "spam" → recommend **SPAM** (move to Bulk Mail)
   - If Rspamd score >= trash threshold (7.0) → recommend **SPAM** (move to Bulk Mail)
   - If Rspamd score >= spam threshold (6.0) → recommend **PROMOTIONAL**
   - If LLM classifies as "promotional/marketing/ads" → recommend **PROMOTIONAL**
   - Otherwise → recommend **KEEP** in inbox
6. **Move emails**: Moves to destination folder (IMAP MOVE/COPY+DELETE, or Graph message move)
7. **Save progress**: Yahoo updates the last UID. M365 updates the delta token

## Command-Line Options

```
usage: inbox-cleaner [-h] [--auto]

Inbox cleaner (Yahoo IMAP + M365 Graph) using Rspamd + LLM classification

options:
  -h, --help  show this help message and exit
  --auto      Automatically apply recommended actions without prompting (overrides INTERACTIVE=true)
```

## Scheduling

### GitHub Actions (Recommended for Cloud Deployment)

The workflow runs hourly on GitHub Actions. The free tier covers 2,000
minutes per month.

**Setup steps:**

1. **Fork or push this repository to GitHub**

2. **Add repository secrets** (Settings → Secrets and variables → Actions):
   - `YAHOO_EMAIL` - your Yahoo email address
   - `YAHOO_PASSWORD` - your Yahoo app password
   - `OPENROUTER_KEY` - your OpenRouter API key

   **Optional: scan an M365 mailbox in the same run.** Add:
   - `M365_TENANT_ID` - Entra tenant (directory) ID
   - `M365_CLIENT_ID` - app registration client ID
   - `M365_CLIENT_SECRET` - app registration client secret value
   - `M365_MAILBOX` - target mailbox UPN (user@tenant.com)

   The workflow leaves `PROVIDERS` unset. The app then enables every provider
   with complete credentials, so setting the four M365 secrets adds M365 to
   the run; removing them scans Yahoo only. See
   [Microsoft 365 Setup](#microsoft-365-setup-optional) for the Azure app
   registration steps.

   **Optional: customize other settings**

   The workflow defines these settings in `.github/workflows/clean-inbox.yml`:
   - LLM model: `openrouter/google/gemini-2.0-flash-exp:free`
   - Spam score threshold: `6.0`
   - Trash score threshold: `7.0`
   - History weight: `0.3`
   - History minimum samples: `3`

   To change them, edit the `.env` creation step in the workflow file.

3. **Enable GitHub Actions** in your repository settings

4. **The workflow runs automatically every hour**
   - Workflow file: `.github/workflows/clean-inbox.yml`
   - Runs at minute 0 of every hour
   - Uses Docker Compose (rspamd + cleaner)
   - Runs in `--auto` mode (no prompts)
   - SQLite state persists between runs as a GitHub artifact

**Manual trigger:**

You can also trigger the workflow manually from the Actions tab:

- Go to Actions → Clean Inbox (Yahoo + M365) → Run workflow

**Monitoring:**

View execution logs in the Actions tab to see:

- How many emails were processed
- Which actions were taken
- Any errors

**Notes:**

- The free tier includes 2,000 minutes per month, which covers hourly runs
- Database state is preserved for 90 days
- Secrets are encrypted and never shown in logs
- Sensitive data stays in repository secrets

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

**Note:** The `--auto` flag makes scheduled runs execute without waiting for
input.

## Email Processing History

The tool stores a complete audit log of processed emails in the SQLite
database:

**Tracked information:**

- Email metadata (sender, subject)
- Rspamd spam score
- LLM classification label
- Recommended action
- Final action taken
- Processing mode (auto or interactive)
- Timestamp

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

- Yahoo has no default "Promotional" folder. The app creates it automatically.
- SQLite stores progress tracking and the email history
- Email read/unread status is preserved during processing
- The app fails when Rspamd is unavailable. Ensure the rspamd service runs.
- LLM classification uses OpenRouter with minimal prompts to keep costs low
- The IMAP backend falls back to COPY + DELETE when the server lacks MOVE

## Architecture

```
inbox-cleaner/
├── pyproject.toml          # uv configuration
├── inbox_cleaner/
│   ├── __init__.py
│   ├── cli.py              # Main CLI entrypoint, provider loop
│   ├── mailbox.py          # Mailbox protocol (IMAP/Graph shared surface)
│   ├── db.py               # SQLite progress tracking
│   ├── imap_client.py      # Yahoo IMAP client (Mailbox impl)
│   ├── m365_client.py      # Microsoft 365 Graph client (Mailbox impl)
│   ├── rspamd.py           # Rspamd HTTP API
│   └── classify.py         # OpenRouter LLM classification
├── Dockerfile              # Container image with uv
├── docker-compose.yml      # Rspamd + cleaner services
├── .env.example            # Configuration template
└── README.md
```

## Troubleshooting

### "Authentication failed"

- Verify you use an App Password, not your regular password
- Check that the email address is correct

### M365 "access denied" / 403 from Graph

- Admin consent was not granted for the application permissions (step 6 in
  the M365 setup)
- `M365_MAILBOX` must be the user's UPN. The app needs the
  `Mail.Read` and `Mail.ReadWrite` **application** permissions, not the
  delegated ones
- A Conditional Access policy or an application access policy can restrict
  the mailboxes the app reads

### M365 token errors

- Check the tenant and client IDs, and that the client secret has not expired
- The secret **Value** is required, not the Secret ID

### "Connection refused" to Rspamd

- Ensure the rspamd service runs: `docker compose up -d rspamd`
- Wait a few seconds for rspamd to start

### "No new emails" but I have unprocessed emails

- Delete `state.sqlite` to reset progress tracking
- The tool processes only emails with a UID above the last processed UID

### LLM classification errors

- Set your OpenRouter API key: `llm keys set openrouter`
- Or add to `.env`: `OPENROUTER_KEY=sk-or-your-key`
- Check that your OpenRouter account has credits
- Ensure `LLM_MODEL` uses the `openrouter/` prefix, for example
  `openrouter/google/gemini-2.5-flash`
- List available models: `llm models list`

### GitHub Actions deployment issues

**Workflow not running:**

- Check that Actions are enabled in repository Settings → Actions → General
- Verify the workflow file is at `.github/workflows/clean-inbox.yml`
- Check the Actions tab for error messages

**Authentication errors:**

- Verify all Yahoo secrets are set: `YAHOO_EMAIL`, `YAHOO_PASSWORD`,
  `OPENROUTER_KEY`
- Use the Yahoo app password, not the regular password
- Secret names must match exactly (case-sensitive)
- For M365, all four secrets must be set: `M365_TENANT_ID`, `M365_CLIENT_ID`,
  `M365_CLIENT_SECRET`, `M365_MAILBOX`
- If the run shows `=== YAHOO ===` but no `=== M365 ===`, the M365 secrets
  are missing. The workflow scans Yahoo only in that case.

**Database not persisting:**

- Check Actions tab → workflow run → Artifacts section
- The artifact named "inbox-cleaner-state" should be uploaded after each run
- The first run has no artifact. This is normal.

**Manually updating the database artifact:**

If you need to modify the database (reset progress, clear history, merge
local changes):

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
   # Copy the modified database to the data directory
   mkdir -p ./data
   cp state.sqlite ./data/state.sqlite

   # Commit temporarily (data/ is in .gitignore, so use -f)
   git add -f ./data/state.sqlite
   git commit -m "temp: database for upload"
   git push

   # Trigger the upload workflow
   gh workflow run upload-db.yml

   # Wait for the upload to finish, then clean up
   sleep 15
   git rm data/state.sqlite
   git commit -m "cleanup: remove temp database"
   git push
   ```

   The `upload-db.yml` workflow uploads `./data/state.sqlite` as the
   `inbox-cleaner-state` artifact. The main `clean-inbox.yml` workflow
   downloads it on the next run.

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

- Check the workflow logs for the "Rspamd is ready!" message
- If a timeout occurs, rspamd may need more startup time
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
