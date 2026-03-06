# Proxmox Scripts API

REST API for deploying VMs/containers from [Proxmox VE Helper Scripts](https://community-scripts.github.io/ProxmoxVE).

## Quick Start

### Installation

See [SERVICE.md](SERVICE.md) for complete setup instructions.

### Environment Variables

The API requires an authentication token. Configure it via systemd service file:

```
Environment=PROXMOX_API_TOKEN=your_secure_token_here
```

See [service/proxmox-api.service](service/proxmox-api.service) for the complete service configuration.

## API Documentation

See [docs/SPEC.md](docs/SPEC.md) for full API specifications.

## Skills

The nanobot assistant uses the skill defined in [skills/proxmox-api/SKILL.md](skills/proxmox-api/SKILL.md).
