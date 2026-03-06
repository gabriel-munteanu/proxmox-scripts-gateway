# Proxmox Scripts API - Specification

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

### Refresh Apps
```bash
POST /apps/refresh
```
Scans the scripts directory and updates the app list.

### Install Status
```bash
GET /install/status
```
Returns current installation status (idle/running, app name, progress).

### Install App
```bash
POST /install
Content-Type: application/json

{
  "app_name": "adguard",
  "vmid": 130,
  "ip": "192.168.1.230/24",
  "cpu": 2,
  "ram": 2048,
  "disk": 4
}
```
Installs the specified application.

## Configuration

- **IP Assignment**: `192.168.1.{vmid + 100}/24` (e.g., VMID 130 → 192.168.1.230/24)
- **Network**: Subnet 192.168.1.0/24, Gateway 192.168.1.1, DNS 192.168.1.201
