"""
Build Container - Python implementation of ProxmoxVE build_container logic

This module replicates the build_container() function from the ProxmoxVE helper scripts.
It handles:
- Network configuration (bridge, IP, gateway, VLAN, etc.)
- Container options (CPU, RAM, disk, features)
- Container creation via pct
- Running install scripts via lxc-attach
"""

import asyncio
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BuildContainerConfig:
    """Configuration for building a container"""
    # Required
    ctid: int
    hostname: str
    ostype: str  # debian, ubuntu, alpine, etc.
    osversion: str  # 12, 22.04, 3.18, etc.
    cores: int = 2
    memory: int = 2048  # MB
    disk_size: int = 4  # GB
    
    # Network
    bridge: str = "vmbr0"
    ip: str = "dhcp"  # or static IP
    gateway: Optional[str] = None
    mac: Optional[str] = None
    vlan: Optional[int] = None
    mtu: Optional[int] = None
    ipv6_method: str = "none"  # auto, dhcp, static, none
    ipv6_addr: Optional[str] = None
    ipv6_gateway: Optional[str] = None
    
    # Storage
    template_storage: str = "local"
    container_storage: str = "local"
    
    # Security
    password: Optional[str] = None
    ssh_keys: Optional[str] = None  # SSH public keys
    unprivileged: bool = True  # CT_TYPE: 1=unprivileged, 0=privileged
    
    # Features
    nesting: bool = True
    keyctl: bool = True  # needed for Docker
    fuse: bool = False
    
    # Additional
    timezone: str = "UTC"
    tags: Optional[str] = None
    protect: bool = False
    gpu_passthrough: bool = False
    
    # App info (for install script)
    app_name: str = ""
    app_install_script: str = ""  # URL to install script


def build_net_string(config: BuildContainerConfig) -> str:
    """Build the network configuration string for pct"""
    net = f"-net0 name=eth0,bridge={config.bridge}"
    
    # MAC address
    if config.mac:
        if config.mac.startswith(","):
            net += config.mac
        else:
            net += f",hwaddr={config.mac}"
    
    # IP
    net += f",ip={config.ip}"
    
    # Gateway
    if config.gateway:
        if config.gateway.startswith(","):
            net += config.gateway
        else:
            net += f",gw={config.gateway}"
    
    # VLAN
    if config.vlan:
        vlan_str = str(config.vlan)
        if vlan_str.startswith(","):
            net += vlan_str
        else:
            net += f",tag={vlan_str}"
    
    # MTU
    if config.mtu:
        mtu_str = str(config.mtu)
        if mtu_str.startswith(","):
            net += mtu_str
        else:
            net += f",mtu={mtu_str}"
    
    # IPv6
    if config.ipv6_method == "auto":
        net += ",ip6=auto"
    elif config.ipv6_method == "dhcp":
        net += ",ip6=dhcp"
    elif config.ipv6_method == "static":
        if config.ipv6_addr:
            net += f",ip6={config.ipv6_addr}"
            if config.ipv6_gateway:
                net += f",gw6={config.ipv6_gateway}"
    
    return net


def build_features_string(config: BuildContainerConfig) -> str:
    """Build the features string for pct"""
    features = []
    
    if config.nesting:
        features.append("nesting=1")
    
    # keyctl only for unprivileged containers
    if config.unprivileged and config.keyctl:
        features.append("keyctl=1")
    
    if config.fuse:
        features.append("fuse=1")
    
    return ",".join(features) if features else ""


def build_pct_options(config: BuildContainerConfig) -> list[str]:
    """Build the pct create options list"""
    options = []
    
    # Hostname
    options.extend(["-hostname", config.hostname])
    
    # Tags
    if config.tags:
        options.extend(["-tags", config.tags])
    
    # Features
    features = build_features_string(config)
    if features:
        options.extend(["-features", features])
    
    # Network
    net_string = build_net_string(config)
    options.append(net_string)
    
    # Onboot
    options.extend(["-onboot", "1"])
    
    # Resources
    options.extend(["-cores", str(config.cores)])
    options.extend(["-memory", str(config.memory)])
    
    # Unprivileged
    options.extend(["-unprivileged", "1" if config.unprivileged else "0"])
    
    # Protection
    if config.protect:
        options.extend(["-protection", "1"])
    
    # Timezone
    tz = config.timezone
    if tz.startswith("Etc/"):
        tz = "host"
    options.extend(["-timezone", tz])
    
    # Password
    if config.password:
        # Password needs special formatting: "-password <password>"
        options.extend(["-password", config.password])
    
    # Root disk
    options.extend(["-rootfs", f"{config.container_storage}:{config.disk_size}"])
    
    return options


def get_install_func_url(ostype: str) -> str:
    """Get the URL for install.func or alpine-install.func"""
    if ostype == "alpine":
        return "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/misc/alpine-install.func"
    return "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/misc/install.func"


def get_install_script_url(app_name: str) -> str:
    """Get the URL for the app install script"""
    return f"https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/install/{app_name}.sh"


async def run_command(cmd: list[str], check: bool = True, capture_output: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result"""
    # For debugging: print command
    print(f"Running: {' '.join(cmd)}")
    
    result = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE if capture_output else None,
        stderr=asyncio.subprocess.PIPE if capture_output else None,
    )
    stdout, stderr = await result.communicate()
    
    if capture_output:
        if stdout:
            print(f"stdout: {stdout.decode()}")
        if stderr:
            print(f"stderr: {stderr.decode()}")
    
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")
    
    return subprocess.CompletedProcess(
        cmd,
        result.returncode,
        stdout.decode() if stdout else "",
        stderr.decode() if stderr else ""
    )


async def validate_storage(storage_name: str, required_gb: int) -> bool:
    """Validate that storage exists and has enough space"""
    try:
        result = await run_command(["pvesm", "status", "-content", "rootdir"])
        # Check if storage supports rootdir
        lines = result.stdout.strip().split("\n")[1:]  # Skip header
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == storage_name and "rootdir" in parts[1]:
                # Storage exists and supports rootdir
                # TODO: Check actual free space
                return True
        return False
    except Exception as e:
        print(f"Storage validation failed: {e}")
        return False


async def create_container(config: BuildContainerConfig, log_callback=None) -> dict:
    """
    Create and configure a container, then run the install script.
    
    Returns a dict with:
    - success: bool
    - ctid: int
    - message: str
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)
        print(msg)
    
    log(f"=== Building Container {config.ctid} ===")
    
    # 1. Validate storage
    log(f"Validating storage '{config.container_storage}'...")
    if not await validate_storage(config.container_storage, config.disk_size):
        return {"success": False, "ctid": config.ctid, "message": f"Storage '{config.container_storage}' not found or doesn't support rootdir"}
    log("Storage validated")
    
    # 2. Get template
    log(f"Getting template for {config.ostype} {config.osversion}...")
    template = f"local:vztmpl/{config.ostype}-{config.osversion}-standard_amd64.tar.gz"
    
    # 3. Build pct create command
    options = build_pct_options(config)
    pct_cmd = ["pct", "create", str(config.ctid), template, *options]
    
    log(f"Creating container with: pct create {config.ctid} {template} {' '.join(options)}")
    
    try:
        await run_command(pct_cmd)
        log("Container created successfully")
    except Exception as e:
        return {"success": False, "ctid": config.ctid, "message": f"Failed to create container: {e}"}
    
    # 4. Start container
    log("Starting container...")
    try:
        await run_command(["pct", "start", str(config.ctid)])
        log("Container started")
    except Exception as e:
        return {"success": False, "ctid": config.ctid, "message": f"Failed to start container: {e}"}
    
    # 5. Wait for container to be running
    await asyncio.sleep(2)
    
    # 6. Run install script if provided
    if config.app_install_script:
        log(f"Running install script: {config.app_install_script}")
        
        # Build the lxc-attach command
        install_cmd = (
            f"lxc-attach -n {config.ctid} -- "
            f"bash -c \"$(curl -fsSL {config.app_install_script})\""
        )
        
        # Execute via bash
        try:
            # Set environment variables for the install script
            env = os.environ.copy()
            env.update({
                "PCT_OSTYPE": config.ostype,
                "PCT_OSVERSION": config.osversion,
                "PCT_DISK_SIZE": str(config.disk_size),
                "CTID": str(config.ctid),
                "CTTYPE": "1" if config.unprivileged else "0",
                "APPLICATION": config.app_name,
                "PASSWORD": config.password or "",
                "SSH_ROOT": config.ssh_keys or "",
                "tz": config.timezone,
            })
            
            # Run the install script
            proc = await asyncio.create_subprocess_shell(
                install_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()
            
            if stdout:
                log(f"Install output: {stdout.decode()}")
            if stderr:
                log(f"Install errors: {stderr.decode()}")
            
            if proc.returncode != 0:
                return {"success": False, "ctid": config.ctid, "message": f"Install script failed with code {proc.returncode}"}
            
            log("Install script completed successfully")
            
        except Exception as e:
            return {"success": False, "ctid": config.ctid, "message": f"Failed to run install script: {e}"}
    
    return {
        "success": True,
        "ctid": config.ctid,
        "message": f"Container {config.ctid} created and configured successfully"
    }


# Convenience function to build config from AppConfig
def build_config_from_app_config(
    app_config,
    app_info: dict,
    ctid: int,
    app_name: str,
) -> BuildContainerConfig:
    """Build BuildContainerConfig from the API's AppConfig"""
    
    # Parse options from app_info
    options = app_info.get("options", {})
    
    # Get OS type and version
    ostype = options.get("os", "debian")
    osversion = options.get("version", "12")
    
    # Network config
    bridge = app_config.bridge or "vmbr0"
    ip = "dhcp"
    gateway = None
    
    # Get IP from options (if provided)
    ip_option = app_config.options.get("ip") if app_config.options else None
    if ip_option:
        # Parse IP format: "192.168.1.100/24,gw=192.168.1.1"
        ip_match = re.match(r"^([^/,]+)", ip_option)
        if ip_match:
            ip = ip_match.group(1)
        
        gw_match = re.search(r"gw=([^,]+)", ip_option)
        if gw_match:
            gateway = gw_match.group(1)
    
    return BuildContainerConfig(
        ctid=ctid,
        hostname=app_name,  # Use app_name as hostname
        ostype=ostype,
        osversion=osversion,
        cores=app_config.cpu,
        memory=app_config.ram_mb,
        disk_size=app_config.disk_gb,
        bridge=bridge,
        ip=ip,
        gateway=gateway,
        password=app_config.options.get("password") if app_config.options else None,
        ssh_keys=app_config.options.get("ssh_key") if app_config.options else None,
        unprivileged=True,  # Default to unprivileged
        timezone="UTC",  # Default timezone
        tags=None,
        app_name=app_name,
        app_install_script=get_install_script_url(app_name),
    )
