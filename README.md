# Autonomous GitHub Maintenance & Development Agent

An autonomous maintenance and development agent that manages GitHub repositories across a 4-phase pipeline using AI coding harnesses (**Antigravity (`agy`)** or **OpenCode (`opencode`)**), ephemeral Docker sandboxes, GitHub App authentication, and HTML email digests.

---

## Pipeline Overview

For each configured repository, the orchestrator runs up to four phases in sequence:

1. **Phase 1 — PR Review Comments Resolver (`src/pipeline/stage1_comments.py`)**
   Finds open PRs authored by the bot (or labeled `agent-fix`) with unresolved review threads, evaluates whether each reviewer remark is technically valid and valuable, applies surgical fixes when warranted, runs tests, pushes changes, and resolves the review threads.
2. **Phase 2 — PR Auto-Merge & Conflict Resolver (`src/pipeline/stage2_merger.py`)**
   Checks open PRs with `APPROVED` reviews and zero unresolved threads, resolves merge conflicts if dirty, verifies CI status checks pass, and squash-merges.
3. **Phase 3 — Ready Issue Implementer (`src/pipeline/stage3_issues.py`)**
   Picks open issues labeled `ready` or `ready to implement`, swaps labels to `in-progress`, creates a feature branch (`agent/issue-<num>-<slug>`), implements the solution via the configured harness, runs tests (with automated repair retry), opens a PR, and labels the issue `ready-for-review`.
4. **Phase 4 — Proactive Code Review & Ideation (`src/pipeline/stage4_ideation.py`)**
   Analyzes repository structure, recent commits, and documentation, deduplicates against issues from the past 90 days, and files high-value improvement proposals labeled `agent-proposal`.

---

## 1. Installation

### Prerequisites
- **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- **Git**
- **Docker** (optional; required for isolated container execution when `sandbox.use_docker: true`)
- **AI Coding Harness CLI**:
  - **Antigravity CLI (`agy`)**: Installed and authenticated on the host (`agy` stores OAuth credentials in `~/.gemini/antigravity-cli/antigravity-oauth-token`)
  - **OpenCode CLI (`opencode`)**: Installed if using `harness: "opencode"` with `DEEPSEEK_API_KEY`

### Install Python Dependencies
```bash
uv sync
# Or using venv + pip:
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## 2. Building the Sandbox Container

When `sandbox.use_docker: true` is enabled in `config.yaml`, [`DockerSandbox`](src/sandbox/container.py) runs commands inside an ephemeral container built from [`Dockerfile.sandbox`](Dockerfile.sandbox).

Build the image before running with Docker enabled:

```bash
docker build -f Dockerfile.sandbox -t repo-agent-sandbox:latest .
```

### How Host `agy` Authentication Works with the Sandbox
1. Log in to `agy` once on the host machine so `~/.gemini/antigravity-cli/antigravity-oauth-token` is created.
2. [`DockerSandbox._run_docker`](src/sandbox/container.py) automatically bind-mounts:
   - `~/.gemini/antigravity-cli` → `/home/runner/.gemini/antigravity-cli` (`rw`, so token refreshes persist)
   - Host `agy` binary → `/home/runner/.local/bin/agy` (`ro`)
3. If Docker is unavailable or `use_docker: false`, [`DockerSandbox`](src/sandbox/container.py) automatically falls back to local host execution.

---

## 3. GitHub App Setup

The agent authenticates as a **GitHub App** so all commits, comments, issues, and PRs are attributed to your bot identity rather than a personal user account.

### Step 1: Create the GitHub App
1. Go to **GitHub Settings → Developer settings → GitHub Apps → New GitHub App**.
2. Set a **GitHub App name** (e.g., `my-coding-agent`) and **Homepage URL**.
3. Disable **Webhook** (unchecked — the agent runs on a schedule/CLI, not webhooks).
4. Configure **Repository permissions**:
   - **Contents**: `Read and write` (cloning, branching, pushing commits)
   - **Pull requests**: `Read and write` (creating PRs, reading/resolving review threads, merging)
   - **Issues**: `Read and write` (reading issues, updating labels, creating proposals)
   - **Checks**: `Read-only` (verifying CI check runs before merging)
   - **Commit statuses**: `Read-only` (verifying status contexts before merging)
   - **Metadata**: `Read-only` (mandatory default)
5. Click **Create GitHub App**.

### Step 2: Generate a Private Key & Note Your App ID
1. On your newly created GitHub App page, note the **App ID** (e.g., `5336083`).
2. Scroll down to **Private keys** and click **Generate a private key**.
3. Save the downloaded `.pem` file into `secrets/` (which is gitignored):
   ```bash
   mkdir -p secrets
   mv ~/Downloads/*.private-key.pem secrets/
   chmod 600 secrets/*.private-key.pem
   ```

### Step 3: Install the App on Your Repositories
1. In your GitHub App settings sidebar, click **Install App** and install it on your account/organization (either for all repositories or selected repositories).
2. The **Installation ID** appears in the URL after installing:
   `https://github.com/settings/installations/<INSTALLATION_ID>`
   *(Note: `installation_id` is optional in `config.yaml`; if omitted, the agent auto-discovers it per repository via the GitHub API).*

### Step 4: Find Your Bot Username & Noreply Email
Every GitHub App has a backing bot user `<app-slug>[bot]`. Query the GitHub Users API to get its numeric user `id`:

```bash
curl -s "https://api.github.com/users/<app-slug>[bot]" | jq '{login, id}'
```

Example response:
```json
{
  "login": "ddvlamin-coding-agent[bot]",
  "id": 343186873
}
```

Use these values in `config.yaml`:
- `bot_username`: `"<login>"` (e.g., `"ddvlamin-coding-agent[bot]"`)
- `bot_email`: `"<id>+<login>@users.noreply.github.com"` (e.g., `"343186873+ddvlamin-coding-agent[bot]@users.noreply.github.com"`)

This ensures GitHub attributes all git commits made by [`GitWorkspace`](src/sandbox/git_workspace.py) to your GitHub App's avatar and profile.

---

## 4. Gmail SMTP Setup (Email Digest Reports)

After each run, [`EmailDigestService`](src/reporting/email_digest.py) sends an HTML summary report via SMTP. To use Gmail:

1. Enable **2-Step Verification** on your Google Account ([myaccount.google.com/security](https://myaccount.google.com/security)).
2. Go to **App passwords** ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)).
3. Create a new App Password (select **Mail** / custom name like `Repo Agent`).
4. Copy the generated 16-character password (without spaces) into `config.yaml` (`smtp.password`) or `.env` (`SMTP_PASSWORD`).
5. Set `smtp.enabled: false` in `config.yaml` (or `SMTP_ENABLED=false`) if you want to disable email dispatch during local testing.

---

## 5. Configuration

Copy the example configuration files and fill in your settings:

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

### Example `config.yaml`
```yaml
github:
  app_id: "5336083"
  installation_id: "171857213"  # Optional; auto-discovered per repo if omitted
  private_key_path: "secrets/my-app.private-key.pem"
  bot_username: "my-coding-agent[bot]"
  bot_email: "343186873+my-coding-agent[bot]@users.noreply.github.com"

smtp:
  host: "smtp.gmail.com"
  port: 587
  user: "alerts@example.com"
  password: "your-16-char-google-app-password"
  from_email: "alerts@example.com"
  to_emails:
    - "maintainer@example.com"
  enabled: true

harness:
  default_harness: "antigravity"  # "antigravity" or "opencode"
  timeout_seconds: 1800
  deepseek_api_key: "${DEEPSEEK_API_KEY}"
  deepseek_base_url: "https://api.deepseek.com"
  agy_path: "agy"
  opencode_path: "opencode"

sandbox:
  use_docker: true
  image: "repo-agent-sandbox:latest"
  memory_limit: "4g"
  cpu_count: 2.0
  timeout_seconds: 1800

repos:
  - name: "owner/repo"
    default_branch: "main"
    test_command: "pytest tests/"
    lint_command: "flake8 src/"
    harness: "antigravity"
    harness_model: "gemini-3.8-flash"
    max_issues_per_run: 2
    ideation_count: 2
    enabled_phases: [1, 2, 3, 4]
```

### Per-Repository Overrides (`.github/agent.yaml`)
Any target repository can include a `.github/agent.yaml` (or `.github/agent.yml`) file to override `default_branch`, `test_command`, `lint_command`, `harness`, `harness_model`, `max_issues_per_run`, `ideation_count`, or `enabled_phases`.

---

## 6. Running the Agent

### Full Orchestrator CLI (`src/main.py`)
```bash
# Dry-run across all configured repositories (no remote modifications)
python -m src.main --all --dry-run

# Live run across all configured repositories
python -m src.main --all

# Run only specific phase(s) on a single repository
python -m src.main --repo owner/repo --phase 1
python -m src.main --repo owner/repo --phase 1 3 --dry-run

# Use a custom config path
python -m src.main --config /path/to/config.yaml --all
```

### Standalone Utility Scripts (`scripts/`)
- **Test Phase 1 (PR Review Comments Resolver) standalone**:
  ```bash
  python scripts/run_stage1.py --dry-run
  python scripts/run_stage1.py
  ```
- **Fetch recent issues (last 90 days) across configured repos**:
  ```bash
  python scripts/fetch_recent_issues.py
  ```

### Scheduled Execution via `systemd`
Sample systemd service and timer units are provided in [`systemd/`](systemd/):
```bash
sudo cp systemd/repo-agent.service systemd/repo-agent.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now repo-agent.timer
```

---

## 7. Running Tests

```bash
pytest tests/
```
