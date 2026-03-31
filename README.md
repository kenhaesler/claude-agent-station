# Claude Agent Station

Self-hosted autonomous Claude Code agent with a web dashboard.

**What it does**: Runs Claude Code agent teams on a schedule to work on your GitHub repositories — implementing features, fixing bugs, and creating issues. A lead agent coordinates teammates that each tackle a single issue. A web dashboard provides real-time visibility into team activity.

## Architecture

```
┌──────────────────────────────────────────────┐
│             Claude Agent Station              │
│                                               │
│   Agent Teams             Web Dashboard       │
│  ┌──────────────┐      ┌──────────────┐      │
│  │  Lead Agent  │      │   FastAPI    │      │
│  │  (Sonnet)    │◄────►│   + Svelte   │      │
│  │    ├─ T1     │      │   + SQLite   │      │
│  │    ├─ T2     │      └──────┬───────┘      │
│  │    └─ T3     │             │               │
│  │  (Opus)      │       :8420 (web UI)        │
│  └──────┬───────┘                             │
│         │                                     │
│    systemd timer                              │
└─────────┴─────────────────────────────────────┘
```

### Agent Teams Model

- **Lead** (Sonnet 4.6): Fetches eligible issues, spawns one teammate per issue, reviews plans for conflicts, monitors until all work completes
- **Teammates** (Opus 4.6): Each works on a single GitHub issue in an isolated git worktree — reads code, plans, implements, tests, commits locally
- **Manager** (Sonnet 4.6): Reviews all teammate work post-completion, issues verdicts: APPROVE (push+merge), PR (human review), REJECT (discard)

Powered by the [Claude Agent SDK](https://docs.anthropic.com/en/docs/claude-code/agent-sdk) with Agent Teams.

### Dashboard

- **Agent Teams Canvas**: Live view of teammates, their current tools, activity feed
- **Command Center**: System overview, run history, token usage
- **Projects**: Add/remove repos, set priority, enable/disable
- **Runs**: View history, costs, verdicts, employee reports, git diffs
- **Logs**: Live WebSocket log streaming, historical search
- **Config**: Models, budgets, rate limits, schedule

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
      "priority": "high",
      "enabled": true
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
