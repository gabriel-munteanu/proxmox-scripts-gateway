# Proxmox VE Helper Scripts API

Auto-install applications from Proxmox VE Helper-Scripts via REST API.

## Overview

Python FastAPI running on Proxmox that dynamically scans 400+ scripts from [Proxmox VE Helper-Scripts](https://community-scripts.github.io/ProxmoxVE) and provides a simple API for container/VM deployment.

## Features

- **Dynamic Script Scanning**: Parses all CT scripts from the helper-scripts repo
- **Auto-update**: `git pull` to fetch latest scripts on each request
- **Installation Locking**: Prevents concurrent installations via file-based lock
- **Token Authentication**: Secure API token via `X-API-Token` header
- **Configurable Resources**: CPU, RAM, disk, bridge per app
- **Auto IP Assignment**: `192.168.1.{vmid + 100}/24`

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/apps` | GET | List all available applications |
| `/apps/{app_name}` | GET | Get app details with default config |
| `/apps/refresh` | POST | Clear apps cache |
| `/install` | POST | Install an application |
| `/install/status` | GET | Check if installation in progress |

## Authentication

Set `PROXMOX_API_TOKEN` environment variable. All endpoints require `X-API-Token` header.

## Installation

```bash
# Clone repo
git clone https://github.com/gabriel-munteanu/proxmox-scripts-gateway.git
cd proxmox-scripts-gateway

# Install dependencies
pip install -r requirements.txt

# Set API token
export PROXMOX_API_TOKEN="your-secure-token"

# Run
python main.py
```

## Workflow

1. User requests `/apps/{app_name}` → API returns defaults (CPU, RAM, disk, IP)
2. User confirms via `/install` → Installation starts
3. Lock prevents concurrent installs
4. Returns VMID, IP, credentials on success

## Configuration

Environment variables:
- `PROXMOX_API_TOKEN` (required) - API authentication token
- `SCRIPTS_DIR` (optional) - Path to helper-scripts repo (default: `/opt/pve-helper-scripts`)
