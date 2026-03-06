# Proxmox Scripts API - Service Setup

## Install Service

1. Copy the service file:
   ```bash
   cp proxmox-api.service /etc/systemd/system/
   ```

2. Edit the token in `/etc/systemd/system/proxmox-api.service`:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
   Replace `YOUR_SECURE_TOKEN_HERE` with the generated token.

3. Enable and start:
   ```bash
   systemctl daemon-reload
   systemctl enable --now proxmox-api
   ```
