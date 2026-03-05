#!/usr/bin/env python3
"""
Proxmox VE Helper Scripts API
Auto-install applications from Proxmox VE Helper-Scripts
"""

import os
import subprocess
import re
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

app = FastAPI(title="Proxmox VE API")

# Security - require API token, no defaults
API_TOKEN = os.environ.get("PROXMOX_API_TOKEN")
if not API_TOKEN:
    raise RuntimeError(
        "Missing PROXMOX_API_TOKEN environment variable. "
        "Set a secure API token before starting the service."
    )

token_header = APIKeyHeader(name="X-API-Token")


async def verify_token(token: str = Depends(token_header)):
    if token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


# Path to Proxmox VE Helper-Scripts repository
SCRIPTS_DIR = Path("/opt/pve-helper-scripts")

# Cache for parsed scripts
APPS_CACHE = None


class AppConfig(BaseModel):
    """Application installation configuration"""
    app_name: str
    cpu: int = Field(default=1, ge=1, le=128, description="Number of CPU cores")
    ram_mb: int = Field(default=512, ge=256, le=131072, description="RAM in MB")
    disk_gb: int = Field(default=4, ge=1, le=1024, description="Disk size in GB")
    bridge: str = "vmbr0"
    # App-specific options (parsed from script)
    options: dict = Field(default_factory=dict)


class InstallResult(BaseModel):
    """Installation result"""
    success: bool
    vmid: Optional[int] = None
    ip: Optional[str] = None
    netmask: str = "24"
    gateway: str = "192.168.1.1"
    dns: str = "192.168.1.201"
    credentials: Optional[dict] = None
    message: str


def get_next_vmid() -> int:
    """Get next available VMID (container)"""
    # Check existing containers
    result = subprocess.run(
        ["pct", "list"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or "Unknown error"
        raise RuntimeError(f"'pct list' failed with exit code {result.returncode}: {error_msg}")

    ids = []
    for line in result.stdout.splitlines()[1:]:  # Skip header
        if line.strip():
            parts = line.split()
            if parts[0].isdigit():
                ids.append(int(parts[0]))

    # Also check VMs
    result = subprocess.run(
        ["qm", "list"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or "Unknown error"
        raise RuntimeError(f"'qm list' failed with exit code {result.returncode}: {error_msg}")

    for line in result.stdout.splitlines()[1:]:
        if line.strip():
            parts = line.split()
            if parts[0].isdigit():
                ids.append(int(parts[0]))

    return max(ids, default=99) + 1


def calculate_ip(vmid: int, base_subnet: str = "192.168.1") -> str:
    """
    Calculate an IPv4 address for a VM/CT by appending `vmid + 100` as the last octet to `base_subnet`.

    This function performs a simple arithmetic offset on the last octet only and does not handle
    overflow beyond 255 or carry into higher octets. Callers must ensure that `vmid + 100` results
    in a valid last-octet value (1-254) for their chosen `base_subnet`.
    """
    last_octet = vmid + 100
    # Ensure the last octet is within a valid IPv4 host range (1-254)
    if not 1 <= last_octet <= 254:
        raise ValueError(
            f"Cannot calculate IP for vmid {vmid}: resulting last octet {last_octet} "
            f"is outside the valid range 1-254."
        )
    return f"{base_subnet}.{last_octet}"


def parse_script(script_path: Path) -> dict:
    """Parse a helper script to extract configuration options"""
    content = script_path.read_text()

    # Extract variables and their defaults
    variables = {}

    # Match common patterns: var="${var:-default}"
    pattern = re.compile(r'(\w+)=["\']?\$\{?(\w+)?:-([^}]+)\}?["\']?')
    for match in pattern.finditer(content):
        var_name, _, default = match.groups()
        variables[var_name] = default.strip('"\'')

    # Extract CT template used
    template_match = re.search(r'CT_TEMPLATE=([^\s]+)', content)
    template = template_match.group(1) if template_match else "debian-12-standard"

    # Extract description
    desc_match = re.search(r'#\s*(Description|Desc):\s*(.+)', content, re.IGNORECASE)
    description = desc_match.group(2) if desc_match else "No description"

    return {
        "name": script_path.stem,
        "description": description,
        "template": template,
        "variables": variables,
        "script_path": str(script_path)
    }


def scan_apps() -> list:
    """Scan scripts directory and parse all apps"""
    global APPS_CACHE

    if APPS_CACHE is not None:
        return APPS_CACHE

    apps = []

    # Try to find scripts directory
    if not SCRIPTS_DIR.exists():
        # Clone if not exists
        subprocess.run(
            ["git", "clone", "https://github.com/community-scripts/ProxmoxVE.git", str(SCRIPTS_DIR)],
            capture_output=True
        )

    ct_scripts = SCRIPTS_DIR / "ct"
    if ct_scripts.exists():
        for script in ct_scripts.glob("*.sh"):
            try:
                app_info = parse_script(script)
                apps.append(app_info)
            except Exception as e:
                print(f"Failed to parse {script}: {e}")

    APPS_CACHE = apps
    return apps


@app.get("/apps")
async def list_apps(token: str = Depends(verify_token)):
    """Get list of available applications"""
    apps = scan_apps()
    return {
        "count": len(apps),
        "apps": [
            {
                "name": a["name"],
                "description": a["description"],
                "template": a["template"]
            }
            for a in apps
        ]
    }


@app.post("/apps/refresh", dependencies=[Depends(verify_token)])
async def refresh_apps_cache():
    """
    Manually clear the applications cache so that scripts in SCRIPTS_DIR
    will be re-parsed on the next access.
    """
    global APPS_CACHE
    APPS_CACHE = None
    return {"status": "ok", "detail": "APPS_CACHE cleared"}


@app.get("/apps/{app_name}")
async def get_app_details(app_name: str, token: str = Depends(verify_token)):
    """Get detailed configuration options for an app"""
    apps = scan_apps()

    app = next((a for a in apps if a["name"].lower() == app_name.lower()), None)
    if not app:
        raise HTTPException(status_code=404, detail=f"App '{app_name}' not found")

    # Calculate next VMID and IP
    next_vmid = get_next_vmid()
    next_ip = calculate_ip(next_vmid)

    return {
        "name": app["name"],
        "description": app["description"],
        "template": app["template"],
        "available_options": app["variables"],
        "defaults": {
            "vmid": next_vmid,
            "ip": f"{next_ip}/24",
            "gateway": "192.168.1.1",
            "dns": "192.168.1.201",
            "cpu": 1,
            "ram_mb": 512,
            "disk_gb": 4
        }
    }


@app.post("/install")
async def install_app(config: AppConfig, token: str = Depends(verify_token)) -> InstallResult:
    """Install an application with given configuration"""
    apps = scan_apps()

    app = next((a for a in apps if a["name"].lower() == config.app_name.lower()), None)
    if not app:
        raise HTTPException(status_code=404, detail=f"App '{config.app_name}' not found")

    # Get VMID (use provided or get next)
    vmid = config.options.get("vmid", get_next_vmid())

    # Calculate IP if not provided
    if "ip" not in config.options:
        ip_base = config.options.get("ip_base", "192.168.1")
        ip = calculate_ip(vmid, ip_base)
    else:
        ip = config.options["ip"]

    # Build environment variables for script
    env = os.environ.copy()
    env.update({
        "CT_ID": str(vmid),
        "CT_CPU": str(config.cpu),
        "CT_RAM": str(config.ram_mb),
        "CT_DISK": str(config.disk_gb * 1000),  # Convert GB to MB (decimal)
        "CT_BRIDGE": config.bridge,
        "CT_IP": ip,
        "CT_GW": "192.168.1.1",
    })

    # Add custom options (only allow specific keys to prevent security issues)
    allowed_keys = {"vmid", "ip", "ip_base", "template", "ssh_key", "password"}
    for key, value in config.options.items():
        if key.lower() in allowed_keys:
            env[f"CT_{key.upper()}"] = str(value)

    try:
        # Execute the script
        result = subprocess.run(
            ["bash", app["script_path"]],
            env=env,
            capture_output=True,
            text=True,
            timeout=600
        )

        if result.returncode != 0:
            return InstallResult(
                success=False,
                message=f"Installation failed: {result.stderr}"
            )

        return InstallResult(
            success=True,
            vmid=vmid,
            ip=f"{ip}/24",
            message=f"Application '{config.app_name}' installed successfully"
        )

    except subprocess.TimeoutExpired:
        return InstallResult(success=False, message="Installation timed out")
    except Exception as e:
        return InstallResult(success=False, message=str(e))


def extract_ip(output: str) -> Optional[str]:
    """Extract IP address from script output"""
    match = re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', output)
    return match.group(0) if match else None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
