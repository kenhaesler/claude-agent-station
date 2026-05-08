# Install

*How to deploy Claude Agent Station on a fresh VM. For anyone setting up their own instance.*

## Prerequisites

- Rocky Linux 9 / RHEL 9 / AlmaLinux 9 (or compatible — CentOS, Oracle Linux, Fedora). The installer warns and asks for confirmation on other distros and requires at minimum OS version 8.x.
- Python 3.11+ (the installer attempts to install it if the system default is older)
- Node.js 18+ / npm (the installer installs Node.js 20 LTS if absent)
- `git`, `jq`, `socat`, `gcc`, `make` (installed automatically by the installer)
- `bubblewrap` (installed automatically if available in the distro's repos)
- Claude Code CLI — installed automatically via npm if absent
- GitHub Personal Access Token with repo scope
- Root / sudo access
- Internet connection (for package downloads and GitHub clone)
- Minimum 2 GB RAM, 5 GB free disk (recommended; installer warns if below)

## Automated install

```bash
git clone https://github.com/kenhaesler/claude-agent-station.git /opt/claude-agent-station
cd /opt/claude-agent-station
sudo bash install.sh
```

You can also run the installer from the cloned directory — it detects local source and copies rather than re-cloning.

**Flags:**

| Flag | Effect |
|------|--------|
| *(none)* | Fresh install |
| `--dry-run` | Print every step without making changes |
| `--upgrade` | Upgrade in place — preserves config, data, and the existing venv |
| `--uninstall` | Remove application files and services (data in `/var/lib/claude-agent-station` is preserved) |

The installer will (in order):

1. Assert it is running as root.
2. Detect the OS (`/etc/os-release`); warn and ask for confirmation on unsupported distros; hard-stop on versions older than 8.x.
3. Check available RAM (warn if < 2 GB) and disk (warn if < 5 GB free on `/`).
4. Install system packages via `dnf`: `python3`, `python3-pip`, `python3-devel`, `git`, `jq`, `socat`, `gcc`, `make`, and `bubblewrap` (if available). Attempt `python3.11` if the default Python is older than 3.11.
5. Install Node.js 20 LTS via `dnf module` or the NodeSource RPM repository (skipped if Node.js 18+ is already present).
6. Install the Claude Code CLI via `npm install -g @anthropic-ai/claude-code` (skipped if `claude` is already on `PATH`).
7. Create the `claude-agent` system user (`--system --create-home --home-dir /home/claude-agent --shell /bin/bash`). Adds the user to the `docker` group if that group exists.
8. Create all required directories: `/opt/claude-agent-station`, `/var/lib/claude-agent-station`, `/var/log/claude-agent`, `/var/log/claude-agent/digests`, `/home/claude-agent/workspaces`, `/home/claude-agent/.config/claude-agent`, `/home/claude-agent/.claude/autonomous`.
9. Copy source to `/opt/claude-agent-station` (or clone from GitHub if running outside the repo).
10. Create a Python venv at `/opt/claude-agent-station/venv` (using `python3.11` or `python3.12` if available, otherwise `python3`). Upgrade `pip`, `setuptools`, `wheel`, then install Python dependencies from `dashboard/backend/requirements-lock.txt` (falls back to `requirements.txt` only if the lock file is absent).
11. Build the Svelte frontend: `npm install` then `npm run build` inside `dashboard/frontend/`.
12. Initialize the SQLite database at `/var/lib/claude-agent-station/station.db` by importing and running `app.database.init_db()`.
13. Write the manager config template to `/home/claude-agent/.claude/autonomous/manager-config.json` (from `agent/config/default-config.json`; skipped if the file already exists).
14. Create the systemd environment file at `/home/claude-agent/.config/claude-agent/environment` with a commented `GH_TOKEN` placeholder (skipped if already present; permissions set to `600`).
15. Copy `agent/systemd/claude-agent.service` and `agent/systemd/claude-agent.timer` to `/etc/systemd/system/`. Write `claude-station-dashboard.service` inline (with `STATION_DB_PATH` and the env file path injected). Run `systemctl daemon-reload`.
16. Configure SELinux (if `getenforce` reports `Enforcing` or `Permissive`): install `policycoreutils-python-utils` if needed, compile `agent/selinux/claude-agent.te`, and load the module with `semodule -i`.
17. Open port 8420/tcp in firewalld (if `firewalld` is active).
18. Set ownership (`claude-agent:claude-agent`) on install, data, and log directories; mark agent scripts executable.
19. `systemctl enable --now claude-station-dashboard.service`; `systemctl enable claude-agent.timer` (timer is enabled but **not** started — it fires on schedule; start manually if you want an immediate run).

When it finishes, the dashboard is running on port 8420. Continue with **First-run walkthrough** below.

## Manual install

For platforms where `install.sh` does not work, or to understand exactly what the automated path does:

### 1. Install system dependencies

```bash
sudo dnf install -y python3.11 python3.11-pip python3.11-devel git jq socat gcc make
```

Install Node.js 20 LTS (needed to build the frontend):

```bash
sudo dnf module enable -y nodejs:20
sudo dnf install -y nodejs npm
```

Install the Claude Code CLI:

```bash
sudo npm install -g @anthropic-ai/claude-code
```

### 2. Create the service user

```bash
sudo useradd --system --create-home --home-dir /home/claude-agent --shell /bin/bash \
    --comment "Claude Agent Station service account" claude-agent
```

### 3. Clone and chown

```bash
sudo git clone https://github.com/kenhaesler/claude-agent-station.git /opt/claude-agent-station
sudo chown -R claude-agent:claude-agent /opt/claude-agent-station
```

### 4. Create the venv and install pinned dependencies

```bash
sudo -u claude-agent python3.11 -m venv /opt/claude-agent-station/venv
sudo -u claude-agent /opt/claude-agent-station/venv/bin/pip install --upgrade pip setuptools wheel
sudo -u claude-agent /opt/claude-agent-station/venv/bin/pip install \
    -r /opt/claude-agent-station/dashboard/backend/requirements-lock.txt
```

### 5. Build the frontend

```bash
cd /opt/claude-agent-station/dashboard/frontend
sudo -u claude-agent npm install --production=false
sudo -u claude-agent npm run build
```

### 6. Create data and log directories

```bash
sudo mkdir -p /var/lib/claude-agent-station \
              /var/log/claude-agent \
              /var/log/claude-agent/digests \
              /home/claude-agent/workspaces \
              /home/claude-agent/.config/claude-agent \
              /home/claude-agent/.claude/autonomous
sudo chown -R claude-agent:claude-agent /var/lib/claude-agent-station /var/log/claude-agent
sudo chown -R claude-agent:claude-agent /home/claude-agent
```

### 7. Initialize the database

```bash
sudo -u claude-agent bash -c "
    export PYTHONPATH=/opt/claude-agent-station/dashboard/backend
    export STATION_DB_PATH=/var/lib/claude-agent-station/station.db
    /opt/claude-agent-station/venv/bin/python3 -c '
import asyncio, sys
sys.path.insert(0, \"/opt/claude-agent-station/dashboard/backend\")
from app.database import init_db
asyncio.run(init_db())
'
"
```

### 8. Install the manager config and environment file

```bash
sudo -u claude-agent cp /opt/claude-agent-station/agent/config/default-config.json \
    /home/claude-agent/.claude/autonomous/manager-config.json
sudo touch /home/claude-agent/.config/claude-agent/environment
sudo chown claude-agent:claude-agent /home/claude-agent/.config/claude-agent/environment
sudo chmod 600 /home/claude-agent/.config/claude-agent/environment
```

Add your GitHub token to that environment file:

```
GH_TOKEN=ghp_your_token_here
```

### 9. Install systemd units

`agent/systemd/` ships five unit files: `claude-agent.service`, `claude-agent.timer`, `claude-agent-validate.service`, `claude-agent-validate.timer`, and `claude-station-dashboard.service`.

The repo's `claude-station-dashboard.service` exists on disk, but it ships with hardcoded paths from a different layout (`/opt/git/claude-agent-station/...`). `install.sh` does not edit it in place — it overwrites `/etc/systemd/system/claude-station-dashboard.service` with a freshly-rendered version pointing at the canonical `/opt/claude-agent-station` install. For a manual install you must do the same: do not just `cp` the repo file unless you also rewrite the paths inside it (e.g. `sudo sed -i 's|/opt/git/claude-agent-station|/opt/claude-agent-station|g' /etc/systemd/system/claude-station-dashboard.service`). The inline template below is authoritative.

Copy the four agent units (these contain no installer-substituted values):

```bash
sudo cp /opt/claude-agent-station/agent/systemd/claude-agent.service          /etc/systemd/system/
sudo cp /opt/claude-agent-station/agent/systemd/claude-agent.timer            /etc/systemd/system/
sudo cp /opt/claude-agent-station/agent/systemd/claude-agent-validate.service /etc/systemd/system/
sudo cp /opt/claude-agent-station/agent/systemd/claude-agent-validate.timer   /etc/systemd/system/
```

Write `/etc/systemd/system/claude-station-dashboard.service` (this matches what the installer renders):

```ini
[Unit]
Description=Claude Agent Station Dashboard Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=claude-agent
Group=claude-agent
WorkingDirectory=/opt/claude-agent-station/dashboard/backend
ExecStart=/opt/claude-agent-station/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8420
Restart=on-failure
RestartSec=5
Environment=PYTHONPATH=/opt/claude-agent-station/dashboard/backend
Environment=STATION_DB_PATH=/var/lib/claude-agent-station/station.db
Environment=HOME=/home/claude-agent
EnvironmentFile=-/home/claude-agent/.config/claude-agent/environment
NoNewPrivileges=yes
ProtectHome=read-only
ReadWritePaths=/var/lib/claude-agent-station /var/log/claude-agent /home/claude-agent
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
```

### 10. Apply SELinux policy (if enforcing)

```bash
getenforce   # Enforcing or Permissive → apply the policy; Disabled → skip
sudo dnf install -y policycoreutils-python-utils selinux-policy-devel
cd /tmp
sudo checkmodule -M -m -o claude-agent.mod /opt/claude-agent-station/agent/selinux/claude-agent.te
sudo semodule_package -o claude-agent.pp -m claude-agent.mod
sudo semodule -i claude-agent.pp
```

### 11. Open the firewall port

```bash
sudo firewall-cmd --permanent --add-port=8420/tcp
sudo firewall-cmd --reload
```

### 12. Start services

```bash
sudo systemctl enable --now claude-station-dashboard.service
sudo systemctl enable claude-agent.timer
```

The agent timer is enabled (fires hourly) but not started yet — start it when you are ready for the first automated run.

### Optional: enable the validate timer

The `claude-agent-validate.timer` unit fires daily at 06:00 to run validate-and-promote checks. The automated installer does not enable it — neither does the manual flow above. Enable it explicitly if you want the daily validation pass:

```bash
sudo systemctl enable --now claude-agent-validate.timer
```

## First-run walkthrough

1. Open `http://<host>:8420/` in a browser and verify the dashboard loads.
2. Authenticate the Claude CLI as the service user:
   ```bash
   sudo -u claude-agent claude login
   ```
3. Add your GitHub token to the environment file:
   ```bash
   sudo -e /home/claude-agent/.config/claude-agent/environment
   # Set: GH_TOKEN=ghp_your_token_here
   sudo systemctl restart claude-station-dashboard.service
   ```
4. Add your first project on the Projects page of the dashboard.
5. Trigger a manual run to verify the pipeline end-to-end:
   ```bash
   sudo systemctl start claude-agent.service
   ```
6. Start the agent timer for scheduled (hourly) runs:
   ```bash
   sudo systemctl start claude-agent.timer
   ```

For the issue lifecycle and how the lead/teammate agent model works, see [`concepts.md`](concepts.md).

## Updating an existing install

```bash
cd /opt/claude-agent-station
sudo -u claude-agent git pull --ff-only
sudo -u claude-agent /opt/claude-agent-station/venv/bin/pip install \
    -r dashboard/backend/requirements-lock.txt
sudo systemctl restart claude-station-dashboard.service
```

Or use the built-in upgrade flag:

```bash
sudo bash install.sh --upgrade
```

CI runs a dependency drift check; if the local install disagrees with the lock file, the dashboard service will fail to start with an import error.

## Updating Python dependencies

The backend uses a two-file pattern for reproducible installs:

| File | Purpose |
|------|---------|
| `dashboard/backend/requirements.txt` | Loose source of truth — direct deps with minimum-version bounds. Edit this. |
| `dashboard/backend/requirements-lock.txt` | Fully pinned (`==`) lock file generated by `pip-compile`, including transitive deps. Production and CI install from this. Do not hand-edit — regenerate via the command below. |
| `dashboard/backend/requirements-dev.txt` | Dev/test tooling (pytest, ruff, …); pulls in the lock file via `-r requirements-lock.txt`. |

To update or add a dependency:

```bash
cd dashboard/backend
# 1. Edit requirements.txt (add/bump a direct dep)
# 2. Regenerate the lock file
pip install pip-tools
pip-compile --allow-unsafe --strip-extras -o requirements-lock.txt requirements.txt
# 3. Commit both files together
```

CI runs a drift check that re-compiles and fails if `requirements-lock.txt` is out of sync with `requirements.txt`.

## Uninstall

Use the installer's built-in flag (preserves data):

```bash
sudo bash install.sh --uninstall
```

Or manually:

```bash
sudo systemctl disable --now \
    claude-station-dashboard.service \
    claude-agent.timer \
    claude-agent.service \
    claude-agent-validate.timer \
    claude-agent-validate.service
sudo rm -f /etc/systemd/system/claude-station-dashboard.service \
           /etc/systemd/system/claude-agent.service \
           /etc/systemd/system/claude-agent.timer \
           /etc/systemd/system/claude-agent-validate.service \
           /etc/systemd/system/claude-agent-validate.timer
sudo systemctl daemon-reload
sudo rm -rf /opt/claude-agent-station
# Close firewall port
sudo firewall-cmd --permanent --remove-port=8420/tcp && sudo firewall-cmd --reload
```

(`install.sh --uninstall` itself only removes the three units it installed — the validate units are silently left behind. The manual command above is more thorough.)

Data and logs are preserved by default. To remove them completely:

```bash
sudo rm -rf /var/lib/claude-agent-station /var/log/claude-agent
sudo userdel -r claude-agent
```
