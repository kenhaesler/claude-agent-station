# Claude Agent Station

[![ci](https://github.com/kenhaesler/claude-agent-station/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kenhaesler/claude-agent-station/actions/workflows/ci.yml)

Self-hosted autonomous Claude Code agent with a web dashboard.

**What it does**: Runs Claude Code agent teams on a schedule to work on your GitHub repositories — implementing features, fixing bugs, and creating issues. A lead agent coordinates three role-specialized teammates per run; issues are decomposed into tasks and distributed across them by specialty. A web dashboard provides real-time visibility into team activity.

## Architecture

```
┌──────────────────────────────────────────────┐
│             Claude Agent Station              │
│                                               │
│   Agent Teams             Web Dashboard       │
│  ┌──────────────┐      ┌──────────────┐      │
│  │  Lead Agent  │      │   FastAPI    │      │
│  │ (Sonnet 4.6) │◄────►│   + Svelte   │      │
│  │    ├─ T1     │      │   + SQLite   │      │
│  │    ├─ T2     │      └──────┬───────┘      │
│  │    └─ T3     │             │               │
│  │  (Opus 4.7)  │       :8420 (web UI)        │
│  └──────┬───────┘                             │
│         │                                     │
│    systemd timer                              │
└─────────┴─────────────────────────────────────┘
```

Powered by the [Claude Agent SDK](https://docs.anthropic.com/en/docs/claude-code/agent-sdk) with Agent Teams. See [Concepts](docs/concepts.md) for what each role does.

## Quick start

```bash
git clone https://github.com/kenhaesler/claude-agent-station.git /opt/claude-agent-station
cd /opt/claude-agent-station
sudo bash install.sh
# Open http://<host>:8420
```

Full prerequisites and manual install steps: [Install guide](docs/install.md).

## Documentation

| | |
|---|---|
| [Install](docs/install.md) | Deploy on a fresh VM |
| [Configuration](docs/configuration.md) | Env vars, models, budgets, project config |
| [Operations](docs/operations.md) | Service control, logs, recovery, upgrade |
| [Concepts](docs/concepts.md) | Agent Teams, verdicts, audit log, plan throttling |
| [Architecture](docs/architecture.md) | Internal structure for contributors |

## Origin

Extracted from [claude-user-memory](https://github.com/VAMFI/claude-user-memory) autonomous mode.

## License

MIT
