# Proxmox Scripts API Skill

This skill provides methods to interact with the Proxmox VE Helper Scripts API.

## Base URL

```
http://<proxmox-host>:8000
```

## Authentication

All requests require header: `X-API-Token: <token>`

Set token via environment variable: `PROXMOX_API_TOKEN`

## Endpoints

### List Apps
```bash
GET /apps
```
Returns list of all available applications.

### Get App Details
```bash
GET /apps/{app_name}
```
Returns app configuration options and defaults (vmid, ip, cpu, ram, disk).

### Refresh Apps Cache
```bash
POST /apps/refresh
```
Clears cached apps and re-parses scripts.

### Check Installation Status
```bash
GET /install/status
```
Returns `{"in_progress": true/false}`.

### Install Application
```bash
POST /install
Content-Type: application/json

{
  "app_name": "adguard",
  "cpu": 2,
  "ram_mb": 1024,
  "disk_gb": 8,
  "bridge": "vmbr0",
  "options": {}
}
```

## IP Assignment Logic

- Formula: `192.168.1.{vmid + 100}/24`
- Example: VMID 130 → IP 192.168.1.230/24
- Gateway: 192.168.1.1
- DNS: 192.168.1.201

## Workflow

1. Call `GET /apps/{app_name}` to get defaults
2. Optionally modify cpu/ram/disk in request
3. Call `POST /install` to start installation
4. Check `/install/status` for progress
5. On success, response includes `vmid` and `ip`

## Installation Lock

- Only one installation can run at a time
- Returns `409 Conflict` if lock is held
- Check `/install/status` before installing
