#!/usr/bin/env python3
"""
Proxmox VE Helper Scripts API
Auto-install applications from Proxmox VE Helper-Scripts
"""

import sys
from pathlib import Path

# Add src directory to path for local modules
sys.path.insert(0, str(Path(__file__).parent))

import os
import subprocess
import re
import fcntl
import asyncio
import secrets
from pathlib import Path
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

from build_container import (
    BuildContainerConfig,
    create_container,
    build_config_from_app_config,
    get_install_script_url,
)

app = FastAPI(title="Proxmox Scripts API")

# Security - require API token, no defaults
API_TOKEN = os.environ.get("PROXMOX_API_TOKEN")
if not API_TOKEN:
    raise RuntimeError(
        "Missing PROXMOX_API_TOKEN environment variable. "
        "Set a secure API token before starting the service."
    )

token_header = APIKeyHeader(name="X-API-Token")


async def verify_token(token: str = Depends(token_header)):
    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


# Path to Proxmox VE Helper-Scripts repository (configurable via env var)
SCRIPTS_DIR = Path(os.environ.get("SCRIPTS_DIR", "/opt/pve-helper-scripts"))

# Cache for parsed scripts
APPS_CACHE = None

# Lock file for installation
LOCK_FILE = Path("/tmp/proxmox-api-install.lock")

# Log directory
LOG_DIR = Path("/var/log/community-scripts")


def get_timestamp() -> str:
    """Get current timestamp in YYYYMMDD-HHMMSS format"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def sanitize_name(name: str) -> str:
    """Replace spaces and special characters with underscores for safe filenames"""
    # Replace spaces and common special chars with underscore
    return re.sub(r'[^\w\-.]', '_', name)


class InstallLock:
    """Context manager for installation lock"""

    def __init__(self):
        self.lock_file = None

    def __enter__(self):
        self.lock_file = open(LOCK_FILE, 'w')
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            self.lock_file.close()
            raise HTTPException(
                status_code=409,
                detail="Installation already in progress. Please wait."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_file:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            self.lock_file.close()


@app.get("/install/status")
async def install_status(token: str = Depends(verify_token)):
    """Check if an installation is in progress"""
    if LOCK_FILE.exists():
        # Try to acquire lock - if we can, no installation is running
        try:
            with open(LOCK_FILE, 'w') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return {"in_progress": False}
        except IOError:
            return {"in_progress": True}
    return {"in_progress": False}


class AppConfig(BaseModel):
    """Application installation configuration"""
    app_name: str
    cpu: int = Field(default=1, ge=1, le=128, description="Number of CPU cores")
    ram_mb: int = Field(default=512, ge=256, le=131072, description="RAM in MB")
    disk_gb: int = Field(default=4, ge=1, le=1024, description="Disk size in GB")
    bridge: str = "vmbr0"
    vmid: Optional[int] = Field(default=None, description="Container ID (optional, auto-assigned if not provided)")
    # App-specific options (parsed from script)
    options: dict = Field(default_factory=dict)

    @field_validator('options')
    @classmethod
    def validate_options(cls, v: dict) -> dict:
        """Validate and sanitize user-provided options"""
        allowed_keys = {"vmid", "ip", "ip_base", "template", "ssh_key", "password"}
        # Filter to only allowed keys and convert vmid to int
        result = {}
        for k, val in v.items():
            if k.lower() not in allowed_keys:
                continue
            if k.lower() == "vmid":
                # Convert vmid to int if possible
                if val is not None:
                    if isinstance(val, int):
                        result[k] = val
                    elif isinstance(val, str):
                        try:
                            result[k] = int(val)
                        except ValueError:
                            raise ValueError("'vmid' must be a valid integer")
                    else:
                        raise ValueError("'vmid' must be an integer")
            else:
                result[k] = val
        return result


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
    # Check if pct command exists (Proxmox only)
    try:
        result = subprocess.run(
            ["pct", "list"],
            capture_output=True,
            text=True,
            timeout=5
        )
    except FileNotFoundError:
        # Not running on Proxmox - use default
        return 100

    if result.returncode != 0:
        # If pct fails, use default
        return 100

    ids = []
    for line in result.stdout.splitlines()[1:]:  # Skip header
        if line.strip():
            parts = line.split()
            if parts[0].isdigit():
                ids.append(int(parts[0]))

    # Also check VMs
    try:
        result = subprocess.run(
            ["qm", "list"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines()[1:]:
                if line.strip():
                    parts = line.split()
                    if parts[0].isdigit():
                        ids.append(int(parts[0]))
    except FileNotFoundError:
        pass  # qm not available

    return max(ids, default=99) + 1


def calculate_ip(vmid: int, base_subnet: str = "192.168.1") -> str:
    """
    Calculate an IPv4 address for a VM/CT based on VMID.
    Uses modulo to ensure the last octet stays in valid range 1-254.
    """
    # Use modulo 253 (max hosts) + 1 to get valid octet 1-254
    last_octet = ((vmid - 100) % 253) + 1 if vmid >= 100 else vmid
    return f"{base_subnet}.{last_octet}"


def parse_script(script_path: Path, script_type: str = "ct") -> dict:
    """
    Parse a helper script to extract configuration options
    
    Args:
        script_path: Path to the script file
        script_type: Type of script - "ct" for containers, "vm" for virtual machines
    """
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

    # Sanitize name for safe filenames
    sanitized_name = sanitize_name(script_path.stem)

    return {
        "name": script_path.stem,
        "sanitized_name": sanitized_name,
        "description": description,
        "template": template,
        "variables": variables,
        "script_path": str(script_path),
        "type": script_type  # "ct" or "vm"
    }


def scan_apps(script_type: str = "ct") -> list:
    """
    Scan scripts directory and parse all apps
    
    Args:
        script_type: Filter by type - "ct" for containers (default), "vm" for virtual machines, "all" for both
    
    Returns:
        List of parsed app dictionaries
    """
    global APPS_CACHE

    # Note: Cache stores ALL apps, filtering happens at return
    if APPS_CACHE is not None:
        if script_type == "all":
            return APPS_CACHE
        return [app for app in APPS_CACHE if app.get("type") == script_type]

    apps = []

    # Handle scripts directory
    if not SCRIPTS_DIR.exists():
        # Clone if not exists
        result = subprocess.run(
            ["git", "clone", "https://github.com/community-scripts/ProxmoxVE.git", str(SCRIPTS_DIR)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"Git clone failed: {result.stderr}")
            return []
    else:
        # Pull if exists
        result = subprocess.run(
            ["git", "-C", str(SCRIPTS_DIR), "pull"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"Git pull failed: {result.stderr}")

    # Scan CT (container) scripts
    ct_scripts = SCRIPTS_DIR / "ct"
    if ct_scripts.exists():
        for script in ct_scripts.glob("*.sh"):
            try:
                app_info = parse_script(script, script_type="ct")
                apps.append(app_info)
            except Exception as e:
                print(f"Failed to parse {script}: {e}")

    # Scan VM scripts (for future use - not enabled by default)
    vm_scripts = SCRIPTS_DIR / "vm"
    if vm_scripts.exists():
        for script in vm_scripts.glob("*.sh"):
            try:
                app_info = parse_script(script, script_type="vm")
                apps.append(app_info)
            except Exception as e:
                print(f"Failed to parse {script}: {e}")

    APPS_CACHE = apps
    
    # Apply filtering
    if script_type == "all":
        return apps
    return [app for app in apps if app.get("type") == script_type]


@app.get("/apps")
async def list_apps(token: str = Depends(verify_token), type: str = "ct"):
    """
    Get list of available applications
    
    Args:
        type: Filter by script type - "ct" (containers, default), "vm" (virtual machines), "all" (both)
    """
    apps = await asyncio.to_thread(scan_apps, script_type=type)
    return {
        "count": len(apps),
        "apps": [
            {
                "name": a["name"],
                "sanitized_name": a.get("sanitized_name", a["name"]),
                "description": a["description"],
                "template": a["template"],
                "type": a.get("type", "ct")
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
    apps = await asyncio.to_thread(scan_apps)

    app = next((a for a in apps if a["name"].lower() == app_name.lower()), None)
    if not app:
        raise HTTPException(status_code=404, detail=f"App '{app_name}' not found")

    # Calculate next VMID and IP
    next_vmid = await asyncio.to_thread(get_next_vmid)
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


def _run_install_with_lock(config: AppConfig) -> InstallResult:
    """Synchronous installation with lock - runs in thread pool"""
    with InstallLock():
        # Run sync version of install
        return _do_install_sync(config)


def _do_install_sync(config: AppConfig) -> InstallResult:
    """Synchronous installation logic (no async calls)"""
    apps = scan_apps()

    # Get app info - use sanitized name for log file
    app = next((a for a in apps if a["name"].lower() == config.app_name.lower()), None)
    if not app:
        raise HTTPException(status_code=404, detail=f"App '{config.app_name}' not found")

    # Check script type (currently only CT is supported)
    if app.get("type") != "ct":
        raise HTTPException(
            status_code=400,
            detail=f"App '{config.app_name}' is a {app.get('type').upper()} script. Only CT (container) scripts are supported currently."
        )

    # Get VMID (use provided or get next), with validation
    raw_vmid = config.vmid if config.vmid is not None else config.options.get("vmid")
    if raw_vmid is not None:
        try:
            vmid = int(raw_vmid)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid 'vmid' option: must be an integer")
    else:
        vmid = get_next_vmid()

    # Calculate IP if not provided
    if "ip" not in config.options:
        ip_base = config.options.get("ip_base", "192.168.1")
        if not isinstance(ip_base, str):
            raise HTTPException(status_code=400, detail="Invalid 'ip_base' option: must be a string")
        ip = calculate_ip(vmid, ip_base)
    else:
        ip = config.options["ip"]
        if not isinstance(ip, str):
            raise HTTPException(status_code=400, detail="Invalid 'ip' option: must be a string")

    # Generate log file path: {timestamp}_{app}_ct{vmid}.log
    # Use sanitized name to handle spaces/special characters in app names
    timestamp = get_timestamp()
    app_name_for_file = app.get("sanitized_name", config.app_name)
    log_filename = f"{timestamp}_{app_name_for_file}_ct{vmid}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / log_filename

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
        # Disable terminal UI features (clear, colors) for non-interactive execution
        "TERM": "dumb",
    })

    # Add custom options (only allow specific keys to prevent security issues)
    allowed_keys = {"vmid", "ip", "ip_base", "template", "ssh_key", "password"}
    for key, value in config.options.items():
        if key.lower() in allowed_keys:
            env[f"CT_{key.upper()}"] = str(value)

    # Build custom fields string for config header (exclude basic fields already listed)
    custom_fields = []
    for key, value in config.options.items():
        if key.lower() not in ("vmid", "ip", "ip_base", "bridge"):  # Skip already-listed fields
            custom_fields.append(f"# {key.upper()}: {value}")

    custom_fields_str = "\n".join(custom_fields)
    if custom_fields_str:
        custom_fields_str = "\n" + custom_fields_str

    # Write configuration header to log file
    config_header = f"""# Installation Configuration
# Timestamp: {timestamp}
# App: {config.app_name}
# Container: ct{vmid}
# CPU: {config.cpu}
# RAM: {config.ram_mb} MB
# Disk: {config.disk_gb} GB
# Bridge: {config.bridge}
# IP: {ip}/24
# Gateway: 192.168.1.1
# DNS: 192.168.1.201
{custom_fields_str}

"""
    log_file.write_text(config_header)

    # Log callback to write to log file
    def log_callback(msg: str):
        with open(log_file, "a") as f:
            f.write(f"# {msg}\n")

    try:
        # Build config for build_container module
        bc_config = build_config_from_app_config(
            app_config=config,
            app_info=app,
            ctid=vmid,
            app_name=config.app_name,
        )
        
        # Execute using the new build_container module
        result = asyncio.run(create_container(bc_config, log_callback=log_callback))

        if not result.get("success"):
            return InstallResult(
                success=False,
                message=result.get("message", "Installation failed")
            )

        return InstallResult(
            success=True,
            vmid=result.get("ctid", vmid),
            ip=f"{ip}/24",
            message=result.get("message", f"Application '{config.app_name}' installed successfully")
        )

    except subprocess.TimeoutExpired:
        with open(log_file, "a") as f:
            f.write("\n# Installation timed out\n")
        return InstallResult(success=False, message="Installation timed out")
    except Exception as e:
        with open(log_file, "a") as f:
            f.write(f"\n# Error: {e}\n")
        return InstallResult(success=False, message=str(e))


@app.post("/install")
async def install_app(config: AppConfig, token: str = Depends(verify_token)) -> InstallResult:
    """Install an application with given configuration"""
    # Run entire install (including lock) in thread to avoid blocking event loop
    return await asyncio.to_thread(_run_install_with_lock, config)


async def _do_install(config: AppConfig) -> InstallResult:
    """Internal installation logic (called after lock acquired)"""
    apps = scan_apps()

    # Get app info - use sanitized name for log file
    app = next((a for a in apps if a["name"].lower() == config.app_name.lower()), None)
    if not app:
        raise HTTPException(status_code=404, detail=f"App '{config.app_name}' not found")

    # Check script type (currently only CT is supported)
    if app.get("type") != "ct":
        raise HTTPException(
            status_code=400,
            detail=f"App '{config.app_name}' is a {app.get('type').upper()} script. Only CT (container) scripts are supported currently."
        )

    # Get VMID (use provided or get next), with validation
    raw_vmid = config.vmid if config.vmid is not None else config.options.get("vmid")
    if raw_vmid is not None:
        try:
            vmid = int(raw_vmid)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid 'vmid' option: must be an integer")
    else:
        vmid = get_next_vmid()

    # Calculate IP if not provided
    if "ip" not in config.options:
        ip_base = config.options.get("ip_base", "192.168.1")
        if not isinstance(ip_base, str):
            raise HTTPException(status_code=400, detail="Invalid 'ip_base' option: must be a string")
        ip = calculate_ip(vmid, ip_base)
    else:
        ip = config.options["ip"]
        if not isinstance(ip, str):
            raise HTTPException(status_code=400, detail="Invalid 'ip' option: must be a string")

    # Generate log file path: {timestamp}_{app}_ct{vmid}.log
    # Use sanitized name for handle spaces/special characters in app names
    timestamp = get_timestamp()
    app_name_for_file = app.get("sanitized_name", config.app_name)
    log_filename = f"{timestamp}_{app_name_for_file}_ct{vmid}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / log_filename

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
        # Disable terminal UI features (clear, colors) for non-interactive execution
        "TERM": "dumb",
    })

    # Add custom options (only allow specific keys to prevent security issues)
    allowed_keys = {"vmid", "ip", "ip_base", "template", "ssh_key", "password"}
    for key, value in config.options.items():
        if key.lower() in allowed_keys:
            env[f"CT_{key.upper()}"] = str(value)

    # Build custom fields string for config header (exclude basic fields already listed)
    custom_fields = []
    for key, value in config.options.items():
        if key.lower() not in ("vmid", "ip", "ip_base", "bridge"):  # Skip already-listed fields
            custom_fields.append(f"# {key.upper()}: {value}")

    custom_fields_str = "\n".join(custom_fields)
    if custom_fields_str:
        custom_fields_str = "\n" + custom_fields_str

    # Write configuration header to log file
    config_header = f"""# Installation Configuration
# Timestamp: {timestamp}
# App: {config.app_name}
# Container: ct{vmid}
# CPU: {config.cpu}
# RAM: {config.ram_mb} MB
# Disk: {config.disk_gb} GB
# Bridge: {config.bridge}
# IP: {ip}/24
# Gateway: 192.168.1.1
# DNS: 192.168.1.201
{custom_fields_str}

"""
    log_file.write_text(config_header)

    try:
        # Execute the script
        result = subprocess.run(
            ["bash", app["script_path"]],
            env=env,
            capture_output=True,
            text=True,
            timeout=600
        )

        # Append output to log file
        log_output = result.stdout + "\n" + result.stderr
        with open(log_file, "a") as f:
            f.write(log_output)

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
        with open(log_file, "a") as f:
            f.write("\n# Installation timed out\n")
        return InstallResult(success=False, message="Installation timed out")
    except Exception as e:
        with open(log_file, "a") as f:
            f.write(f"\n# Error: {e}\n")
        return InstallResult(success=False, message=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
