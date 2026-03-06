# Proxmox API Service Installation

## Quick Setup

1. **Copy the service file to systemd:**
   ```bash
   cp proxmox-api.service /etc/systemd/system/
   ```

2. **Edit the environment variables:**
   ```bash
   nano /etc/systemd/system/proxmox-api.service
   ```
   
   Change `YOUR_SECURE_TOKEN_HERE` to a secure token:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

3. **Reload systemd and enable the service:**
   ```bash
   systemctl daemon-reload
   systemctl enable proxmox-api
   systemctl start proxmox-api
   ```

## Commands

| Action | Command |
|--------|---------|
| Start | `systemctl start proxmox-api` |
| Stop | `systemctl stop proxmox-api` |
| Restart | `systemctl restart proxmox-api` |
| Status | `systemctl status proxmox-api` |
| Logs | `journalctl -u proxmox-api -f` |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PROXMOX_API_TOKEN` | Yes | - | API authentication token |
| `SCRIPTS_DIR` | No | `/opt/pve-helper-scripts` | Path to ProxmoxVE scripts |
| `API_HOST` | No | `0.0.0.0` | API listen address |
| `API_PORT` | No | `8000` | API listen port |
