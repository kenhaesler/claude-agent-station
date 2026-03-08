# Claude Agent Station

Self-hosted autonomous Claude Code agent with a web dashboard.

**What it does**: Runs Claude Code agents on a schedule to work on your GitHub repositories — implementing features, fixing bugs, analyzing codebases, and creating issues. A web dashboard lets you monitor runs, configure projects, view logs, and manage the system.

## Architecture

```
┌─────────────────────────────────────────┐
│           Claude Agent Station           │
│                                          │
│   Agent Core          Web Dashboard      │
│  ┌──────────┐      ┌──────────────┐     │
│  │ Manager  │      │   FastAPI    │     │
│  │ Employee │◄────►│   + Svelte   │     │
│  │ Analyst  │      │   + SQLite   │     │
│  └────┬─────┘      └──────┬───────┘     │
│       │                   │              │
│  systemd timer      :8420 (web UI)       │
└───────┴───────────────────┴──────────────┘
```

### Manager/Employee Model

- **Employee** (Opus 4.6): Picks up GitHub issues, implements solutions, runs tests, commits locally
- **Analyst** (Sonnet 4.6): Reads codebase, creates high-quality GitHub issues (cheaper, read-only)
- **Manager** (Sonnet 4.6): Reviews employee work, issues verdicts: APPROVE (push+merge), PR (human review), REJECT (discard)

### Dashboard

- **Projects**: Add/remove repos, set mode (full/analyze), priority, enable/disable
- **Runs**: View history, costs, verdicts, employee reports, git diffs
- **Logs**: Live WebSocket log streaming, historical search
- **Config**: Models, budgets, rate limits, schedule
- **System**: VM health, systemd status, auth status, circuit breaker

## Quick Start

```bash
# Install on Rocky Linux 9 / RHEL-based
sudo bash install.sh

# Or manually:
# 1. Install deps: python3, pip, git, jq, claude-cli
# 2. Create claude-agent user
# 3. Set up systemd units
# 4. Start dashboard: systemctl start claude-station-dashboard
# 5. Open http://localhost:8420
```

## Configuration

Add projects via the web dashboard or edit the config directly:

```json
{
  "projects": [
    {
      "repo": "owner/repo",
      "mode": "full",
      "priority": "high"
    }
  ]
}
```

## Requirements

- Linux VM (Rocky Linux 9 recommended)
- Claude Code CLI with valid authentication
- GitHub token with repo access
- Python 3.11+

## Origin

Extracted from [claude-user-memory](https://github.com/VAMFI/claude-user-memory) autonomous mode. See `ARCHITECTURE.md` for full system design.

## License

MIT
