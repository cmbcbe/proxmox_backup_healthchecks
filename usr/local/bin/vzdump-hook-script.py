#!/usr/bin/env python3
"""
Proxmox VZDump Backup Hook Script with Healthchecks.io integration.

This script runs at various phases of the Proxmox Backup task to monitor
and report backup status to Healthchecks.io or self-hosted

Author: Cédric MARCOUX
Localisation : Aywaille, Belgium
Version: 1.0.0

Release Notes:
- **1.0.0 (2025-03-12)**:
  - Initial release

Installation:
1. Place this script at: /usr/local/bin/vzdump-hook-script.py
2. Make it executable: chmod +x /usr/local/bin/vzdump-hook-script.py
3. Create a variables.env file at: /etc/pve/healthchecks/variables.env
4. Configure the script in Proxmox backup jobs:
   - Add to /etc/pve/jobs.cfg: script /usr/local/bin/vzdump-hook-script.py

Environment variables can be configured in /etc/pve/healthchecks/variables.env:
    HC_BASE_DOMAIN=https://healthchecks.example.com or <self-hosted url>
    HC_PING_DOMAIN=https://healthchecks.example.com/ping or <self-hosted url>/ping
    HC_RW_API_KEY=<your project read-write api key>
    HC_PING_KEY=<your project ping key>

Job execution flow:
UPID:$node:$pid:$pstart:$starttime:$dtype:$id:$user
PID/PSTART/STARTTIME are HEX encoded.
This TASK_ID can be used to grab the full job log from `/var/log/pve/tasks/{ALPHANUM}/${TASK_ID}`
ALPHANUM is determined by the last character of the `starttime`.
"""

import os
import sys
import json
import re
import subprocess
import shutil
import tempfile
import requests
import argparse
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any, Union

# Default configuration
DEFAULT_HC_BASE_DOMAIN = "https://healthchecks.cmbc.be"
DEFAULT_HC_PING_DOMAIN = "https://healthchecks.cmbc.be/ping"
DEFAULT_HC_RW_API_KEY = "eJy4I0V41OJ7jM1tQy5gjtBgyJ9mz1Kd"
DEFAULT_HC_PING_KEY = "oiB2qNQV2uGsYSxQAo3rxA"
DEFAULT_ERROR_CODE = 666
ENV_FILE = "/etc/pve/healthchecks/variables.env"

# Load environment variables from file
def load_env_file(file_path=ENV_FILE):
    """Load environment variables from a file."""
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as file:
                for line in file:
                    # Skip comments and empty lines
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse key=value pairs
                    if '=' in line:
                        key, value = line.split('=', 1)
                        # Remove quotes if present
                        value = value.strip('\'"')
                        os.environ[key] = value
            return True
        except Exception as e:
            print(f"Error loading environment file {file_path}: {e}", file=sys.stderr)
    return False

# Load environment variables from the file
load_env_file()

# Get configuration from environment variables or use defaults
HC_BASE_DOMAIN = os.environ.get("HC_BASE_DOMAIN", DEFAULT_HC_BASE_DOMAIN)
HC_PING_DOMAIN = os.environ.get("HC_PING_DOMAIN", DEFAULT_HC_PING_DOMAIN)
HC_RW_API_KEY = os.environ.get("HC_RW_API_KEY", DEFAULT_HC_RW_API_KEY)
HC_PING_KEY = os.environ.get("HC_PING_KEY", DEFAULT_HC_PING_KEY)

# Command line arguments - these would override environment variables
def parse_args():
    parser = argparse.ArgumentParser(description="Proxmox Backup Hook Script with Healthchecks.io integration")
    
    # Arguments for backup phases - these are positional for compatibility
    parser.add_argument("phase", nargs="?", default="", help="Backup phase (e.g. job-init, job-start, backup-start)")
    parser.add_argument("mode", nargs="?", default="", help="Backup mode")
    parser.add_argument("vmid", nargs="?", default="", help="VM or LXC ID")
    
    # Arguments for Healthchecks configuration
    parser.add_argument("--hc-domain", 
                        help=f"Healthchecks domain (default: {HC_BASE_DOMAIN})")
    parser.add_argument("--hc-ping-domain", 
                        help=f"Healthchecks ping domain (default: {HC_PING_DOMAIN})")
    parser.add_argument("--hc-rw-key", 
                        help="Healthchecks read-write API key")
    parser.add_argument("--hc-ping-key", 
                        help="Healthchecks ping key")
    parser.add_argument("--env-file", default=ENV_FILE,
                        help=f"Path to environment file (default: {ENV_FILE})")
    
    args = parser.parse_args()
    return args

# Parse command line arguments
args = parse_args()

# Override environment variables with command line arguments if provided
if args.env_file and args.env_file != ENV_FILE:
    load_env_file(args.env_file)

HC_BASE_DOMAIN = args.hc_domain or HC_BASE_DOMAIN
HC_PING_DOMAIN = args.hc_ping_domain or HC_PING_DOMAIN
HC_RW_API_KEY = args.hc_rw_key or HC_RW_API_KEY
HC_PING_KEY = args.hc_ping_key or HC_PING_KEY

# Get task ID
def get_task_id() -> str:
    """Get the task ID from the parent process."""
    try:
        ppid = os.getppid()
        cmd = f"ps -o args= {ppid}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip()
    except Exception as e:
        error("Get Task ID", str(e))
        return "unknown-task"

TASK_ID = get_task_id()
print(f"HOOK: {' '.join(sys.argv[1:])} -- {TASK_ID}")

# Create error log file
ERRLOG = Path(tempfile.gettempdir()) / f"{TASK_ID}.errlog"

# Utility functions
def error(task: str, msg: str = "", exit_code: int = DEFAULT_ERROR_CODE) -> None:
    """Log an error and exit the script."""
    error_msg = f"FATAL: '{task}' failed with exit code {exit_code}."
    if msg:
        error_msg += f"\nCONTEXT: {msg}"
    
    print(error_msg, file=sys.stderr)
    with open(ERRLOG, "a") as f:
        f.write(f"{error_msg}\n")
    
    sys.exit(exit_code)

def warn(task: str, msg: str = "") -> None:
    """Log a warning."""
    warn_msg = f"WARNING: '{task}' issue."
    if msg:
        warn_msg += f"\nCONTEXT: {msg}"
    
    print(warn_msg, file=sys.stderr)
    with open(ERRLOG, "a") as f:
        f.write(f"{warn_msg}\n")

def info(msg: str) -> None:
    """Log information."""
    import inspect
    caller = inspect.getframeinfo(inspect.currentframe().f_back)
    print(f"MESG: '{caller.function}:{caller.lineno}' {msg}")

def get_cluster_info() -> Tuple[str, str]:
    """Get information about cluster and node."""
    try:
        cmd = "pvesh get /cluster/status --output-format json"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            error("Get Cluster Info", f"Failed to execute pvesh: {result.stderr}")
        
        data = json.loads(result.stdout)
        
        # Determine if we are in a cluster
        cluster = "standalone"
        for item in data:
            if item.get("type") == "cluster":
                cluster = item.get("name", "unknown-cluster")
                break
        
        # Find local node
        node = None
        for item in data:
            if item.get("type") == "node" and item.get("local") == 1:
                node = item.get("name")
                break
        
        if not node:
            cmd = "hostname"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            node = result.stdout.strip()
        
        return cluster, node
    
    except Exception as e:
        error("Get Cluster Info", str(e))
        return "unknown-cluster", "unknown-node"

# Get cluster and node information
CLUSTER, NODE = get_cluster_info()

# Define slug variables
def get_domain() -> str:
    """Get the host domain."""
    try:
        cmd = "hostname --domain"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip()
    except Exception:
        return "local"

HC_SLUG_SUFFIX = f"{NODE.lower()}.{CLUSTER.lower()}.{get_domain().lower()}"
HC_HOST_SLUG_PREFIX = "job"

# Process command line arguments
PHASE = args.phase
if not PHASE:
    error("Arguments", "Phase not provided")

MODE = args.mode
VMID = args.vmid

# Environment variables
VMTYPE = os.environ.get("VMTYPE", "unknown")
HC_VM_SLUG_PREFIX = f"{VMID}-{VMTYPE}"

# DUMPDIR is set for local backups, STOREID for Proxmox Backup Server
STORAGE = os.environ.get("DUMPDIR", os.environ.get("STOREID", ""))

# Log file
def get_logfile() -> str:
    """Get the log file path."""
    fallback = f"/var/log/vzdump/{VMTYPE.lower()}-{VMID}.log"
    return os.environ.get("LOGFILE", fallback)

LOGFILE = get_logfile()

# Function to normalize a slug
def slugify(text: str, suffix: str = "") -> str:
    """Normalize text to create a slug."""
    if suffix:
        text = f"{text} {suffix}"
    
    # Replace spaces with double underscores
    text = text.replace(" ", "__")
    
    # Replace periods with hyphens
    text = text.replace(".", "-")
    
    # Keep only alphanumeric characters, hyphens and underscores
    text = re.sub(r"[^a-zA-Z0-9_-]", "_", text)
    
    return text

# Function to get the dashboard URL from a slug
def get_dashboard_url(slug: str, apikey: str, base_url: str = "https://healthchecks.io") -> str:
    """Get the dashboard URL from a slug."""
    slug = slugify(slug, HC_SLUG_SUFFIX)
    url = f"{base_url}/api/v3/checks/?slug={slug}"
    
    try:
        headers = {"X-Api-Key": apikey}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Extract ping URL and convert to dashboard URL
        ping_url = data["checks"][0]["ping_url"]
        dashboard_url = ping_url.replace("ping", "checks") + "/details"
        
        return dashboard_url
    
    except Exception as e:
        error("Get Dashboard URL", str(e))
        return ""

# Functions to create tags
def add_tag(key: str, value: str) -> Optional[str]:
    """Create a tag in the format key=value."""
    if not key:
        error("Key Validation", "Empty key")
        return None
    
    if not value:
        #warn("Value Validation", "Empty value")
        return None
    
    # Check that the value contains useful characters
    if value.strip():
        # Normalize the tag
        return f" {key}={value.replace(' ', '_')}"
    
    return None

def add_tag_from_file(filepath: str, key: str = "") -> Optional[str]:
    """Create a tag from a file's content."""
    file_path = Path(filepath)
    
    if not file_path.exists():
        error("Check file exists", f"File {filepath} not found")
        return None
    
    if not file_path.is_file():
        error("Check file type", f"{filepath} is not a file")
        return None
    
    if not os.access(file_path, os.R_OK):
        error("Check file permissions", f"Cannot read {filepath}")
        return None
    
    if not key:
        key = file_path.name
    
    value = file_path.read_text().strip()
    return add_tag(key.replace(" ", "_"), value)

def add_tag_from_cmd(key: str, *cmd_args) -> Optional[str]:
    """Create a tag from a command's output."""
    cmd = cmd_args[0]
    if not shutil.which(cmd):
        error("Validating command", f"Command {cmd} not found")
        return None
    
    try:
        result = subprocess.run(cmd_args, capture_output=True, text=True, check=True)
        return add_tag(key.replace(" ", "_"), result.stdout.strip())
    except subprocess.CalledProcessError as e:
        error("Tag Command Execution", str(e))
        return None

# Fonction pour créer ou mettre à jour un endpoint Healthchecks
def hc_create(name: str, slug_prefix: str, grace: int = 3600, description: str = "", 
              tags: str = "", channels: str = "*", timeout: int = 86400, 
              apikey: str = HC_RW_API_KEY, url: str = HC_BASE_DOMAIN) -> None:
    """Crée ou met à jour un endpoint Healthchecks."""
    if not name:
        error("HC Create", "Name parameter is required")
    
    if not slug_prefix:
        error("HC Create", "Slug prefix parameter is required")
    
    if not apikey:
        error("HC Create", "API key is required")
    
    # Construire le slug complet
    slug = slugify(slug_prefix, HC_SLUG_SUFFIX)
    
    # Récupérer le fuseau horaire
    try:
        tz_cmd = "timedatectl show --property Timezone | cut -d '=' -f2"
        tz_result = subprocess.run(tz_cmd, shell=True, capture_output=True, text=True, check=True)
        timezone = tz_result.stdout.strip()
    except Exception:
        timezone = "UTC"
    
    # Normaliser les tags
    tags = tags.strip().lower()
    
    # Construire la charge utile
    payload = {
        "name": name,
        "slug": slug,
        "channels": channels,
        "timeout": timeout,
        "tz": timezone,
        "grace": grace,
        "desc": description,
        "tags": tags,
        "unique": ["name"]
    }
    
    # Envoyer la requête
    info(f"Creating/Updating check {slug}")
    api_url = f"{url}/api/v3/checks/"
    
    try:
        headers = {"X-Api-Key": apikey}
        response = requests.post(api_url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        
        # Vérifier que la réponse est un JSON valide
        response.json()
    except requests.exceptions.RequestException as e:
        error("Healthcheck Creation", str(e))

# Fonction pour envoyer un ping à un endpoint Healthchecks
def hc_ping(slug_prefix: str, report: str = "", file: str = "", data: str = "",
            pingkey: str = HC_PING_KEY, url: str = HC_PING_DOMAIN) -> None:
    """Envoie un ping à un endpoint Healthchecks."""
    if not slug_prefix:
        error("HC Ping", "Slug prefix parameter is required")
    
    if not pingkey:
        error("HC Ping", "Ping key is required")
    
    # Vérifier que le rapport est valide
    valid_reports = ["", "start", "fail", "log"]
    if report and report not in valid_reports and not report.isdigit():
        error("HC Ping", f"Report status must be blank, start, fail, log, or a positive number. Got - '{report}'")
    
    # Vérifier que file et data ne sont pas tous les deux fournis
    if file and data:
        error("HC Ping", "File and Data arguments are mutually exclusive")
    
    # Construire le slug complet
    slug = slugify(slug_prefix, HC_SLUG_SUFFIX)
    
    # Construire l'URL complet
    ping_url = f"{url}/{pingkey}/{slug}"
    if report:
        ping_url += f"/{report}"
    
    info(f"PING ENDPOINT: {ping_url}")
    
    # Récupérer les données du fichier si nécessaire
    if file:
        file_path = Path(file)
        if not file_path.exists():
            error("Check file exists", f"File {file} not found")
        
        if not file_path.is_file():
            error("Check file type", f"{file} is not a file")
        
        if not os.access(file_path, os.R_OK):
            error("Check file permissions", f"Cannot read {file}")
        
        try:
            with open(file, "r") as f:
                # Lire les derniers 100000 octets
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 100000), os.SEEK_SET)
                data = f.read()
        except Exception as e:
            error("Read Log File", str(e))
    
    # Envoyer le ping
    try:
        response = requests.post(ping_url, data=data, timeout=10)
        response.raise_for_status()
        return response.url
    except requests.exceptions.RequestException as e:
        error(f"Healthcheck Ping w/Data ({report or 'success'})", str(e))

# Vérifier que jq est installé
def check_jq_installed() -> None:
    """Vérifie que jq est installé et l'installe si nécessaire."""
    if not shutil.which("jq"):
        info("JQ could not be found, installing")
        
        if shutil.which("apt"):
            try:
                subprocess.run(["apt", "install", "-y", "jq"], check=True)
            except subprocess.CalledProcessError:
                error("Installing jq", "apt install failed")
        
        elif shutil.which("dnf"):
            try:
                subprocess.run(["dnf", "install", "-y", "jq"], check=True)
            except subprocess.CalledProcessError:
                error("Installing jq", "dnf install failed")
        
        else:
            error("Installing jq", "Unknown package manager")

# Exécuter les actions en fonction de la phase
def main() -> None:
    """Main function that executes actions based on the phase."""
    # Display configuration parameters for debugging
    info(f"Configuration: HC_BASE_DOMAIN={HC_BASE_DOMAIN}, HC_PING_DOMAIN={HC_PING_DOMAIN}")
    info(f"Using API Key={HC_RW_API_KEY[:4]}***{HC_RW_API_KEY[-4:]} and Ping Key={HC_PING_KEY[:4]}***{HC_PING_KEY[-4:]}")
    
    check_jq_installed()
    
    if PHASE == "job-init":
        # Créer l'endpoint Healthchecks pour l'hôte
        tags = ""
        
        # Ajouter des tags
        for tag_data in [
            add_tag("cluster", CLUSTER),
            add_tag("node", os.environ.get("HOSTNAME", "")),
            add_tag("storage", STORAGE),
            add_tag_from_file("/etc/machine-id"),
            add_tag_from_cmd("arch", "uname", "--machine"),
            add_tag_from_cmd("kernel", "uname", "--kernel-release")
        ]:
            if tag_data:
                tags += tag_data
        
        # Récupérer la description du nœud
        try:
            cmd = "grep '^#' /etc/pve/local/config | sed 's/^#//'"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            node_description = result.stdout.strip()
        except Exception:
            node_description = ""
        
        info(f"{PHASE} -- Create {os.environ.get('HOSTNAME', 'unknown')} Endpoint")
        hc_create(
            name=HC_SLUG_SUFFIX,
            slug_prefix=HC_HOST_SLUG_PREFIX,
            grace=7200,
            description=node_description,
            tags=tags
        )
    
    elif PHASE == "job-start":
        # Envoyer un ping de démarrage à l'endpoint de l'hôte
        info(f"{PHASE} -- Ping Host Start")
        hc_ping(HC_HOST_SLUG_PREFIX, "start")
    
    elif PHASE == "backup-start":
        # Créer et démarrer l'endpoint Healthchecks pour la VM/LXC
        # et envoyer un ping de journal à l'endpoint de l'hôte
        tags = ""
        
        # Ajouter des tags
        for tag_data in [
            add_tag("cluster", CLUSTER),
            add_tag_from_cmd("node", "hostname"),
            add_tag("storage", STORAGE),
            add_tag("mode", MODE),
            add_tag("vmid", VMID),
            add_tag("hostname", os.environ.get("HOSTNAME", "")),
            add_tag("type", os.environ.get("HOSTTYPE", "")),
            add_tag("vmtype", VMTYPE)
        ]:
            if tag_data:
                tags += tag_data
        
        # Récupérer la description de la VM/LXC
        description = ""
        if VMTYPE == "qemu":
            try:
                cmd = f"grep '^#' /etc/pve/local/qemu-server/{VMID}.conf | sed 's/^#//'"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                description = result.stdout.strip()
            except Exception:
                pass
        elif VMTYPE == "lxc":
            try:
                cmd = f"grep '^#' /etc/pve/local/lxc/{VMID}.conf | sed 's/^#//'"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                description = result.stdout.strip()
            except Exception:
                pass
        
        info(f"{PHASE} -- Create {os.environ.get('HOSTNAME', 'unknown')} Endpoint")
        
        # Créer l'endpoint pour la VM/LXC
        domain_cmd = "hostname --domain"
        domain_result = subprocess.run(domain_cmd, shell=True, capture_output=True, text=True)
        domain = domain_result.stdout.strip()
        
        hc_create(
            name=f"{NODE}.{domain}.{VMTYPE}.{VMID}.{os.environ.get('HOSTNAME', 'unknown')}",
            slug_prefix=HC_VM_SLUG_PREFIX,
            grace=3600,
            description=description,
            tags=tags
        )
        
        info(f"{PHASE} -- Ping {VMTYPE} start")
        hc_ping(HC_VM_SLUG_PREFIX, "start")
        
        info(f"{PHASE} -- Ping Host Log")
        hc_ping(HC_HOST_SLUG_PREFIX, "log", data=f"{PHASE}: {HC_VM_SLUG_PREFIX}")
    
    elif PHASE in ["pre-stop", "pre-restart", "post-restart"]:
        # Envoyer un ping de journal aux endpoints de la VM/LXC et de l'hôte
        info(f"{PHASE} -- Ping {VMTYPE} Log")
        hc_ping(HC_VM_SLUG_PREFIX, "log", data=f"{PHASE}: {HC_VM_SLUG_PREFIX}")
        
        info(f"{PHASE} -- Ping Host Log")
        hc_ping(HC_HOST_SLUG_PREFIX, "log", data=f"{PHASE}: {HC_VM_SLUG_PREFIX}")
    
    elif PHASE == "backup-end":
        # Envoyer un ping de succès à l'endpoint de la VM/LXC
        # et un ping de journal à l'endpoint de l'hôte
        info(f"{PHASE} -- Ping {VMTYPE} Success")
        hc_ping(HC_VM_SLUG_PREFIX, data=f"{PHASE}: {HC_VM_SLUG_PREFIX}")
        
        info(f"{PHASE} -- Ping Host Log")
        hc_ping(HC_HOST_SLUG_PREFIX, "log", data=f"{PHASE}: {HC_VM_SLUG_PREFIX}")
    
    elif PHASE == "backup-abort":
        # Envoyer un ping d'échec à l'endpoint de la VM/LXC
        # et un ping de journal à l'endpoint de l'hôte
        info(f"{PHASE} -- Ping {VMTYPE} fail")
        hc_ping(HC_VM_SLUG_PREFIX, "fail", data=f"{PHASE}: {HC_VM_SLUG_PREFIX}")
        
        info(f"{PHASE} -- Ping Host Log")
        hc_ping(HC_HOST_SLUG_PREFIX, "log", data=f"{PHASE}: {HC_VM_SLUG_PREFIX}")
        
        url = get_dashboard_url(HC_VM_SLUG_PREFIX, HC_RW_API_KEY, HC_BASE_DOMAIN)
        with open(ERRLOG, "a") as f:
            f.write(f"{TASK_ID} - {VMID} - Backup Abort - {url}\n")
        print(f"{TASK_ID} - {VMID} - Backup Abort - {url}")
    
    elif PHASE == "log-end":
        # Envoyer un ping de journal à l'endpoint de la VM/LXC
        # avec le contenu du fichier journal
        info(f"LOGFILE: {LOGFILE}")
        info(f"{PHASE} -- Ping {VMTYPE} Log")
        
        try:
            # Filtrer le journal pour ne pas inclure les lignes MESG et OKhttp
            cmd = f"grep -v 'MESG' {LOGFILE} | grep -v 'OKhttp'"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            backup_log = result.stdout
            
            hc_ping(HC_VM_SLUG_PREFIX, "log", data=backup_log)
        except Exception as e:
            error("Read Log File", str(e))
    
    elif PHASE == "job-end":
        # Envoyer un ping de succès ou d'échec à l'endpoint de l'hôte
        if ERRLOG.exists():
            with open(ERRLOG, "r") as f:
                job_log = f.read()
            
            info(f"{PHASE} -- Ping Host Fail")
            hc_ping(HC_HOST_SLUG_PREFIX, "fail", data=job_log)
            ERRLOG.unlink()  # Supprimer le fichier d'erreur
        else:
            info(f"{PHASE} -- Ping Host Success")
            hc_ping(HC_HOST_SLUG_PREFIX, data=TASK_ID)
    
    elif PHASE == "job-abort":
        # Envoyer un ping d'échec à l'endpoint de l'hôte
        info(f"{PHASE} -- Ping Host Fail")
        with open(ERRLOG, "a") as f:
            f.write(f"{TASK_ID} - Job Abort\n")
        
        with open(ERRLOG, "r") as f:
            job_log = f.read()
        
        hc_ping(HC_HOST_SLUG_PREFIX, "fail", data=job_log)
    
    else:
        # Phase inconnue, envoyer un ping d'échec à l'endpoint de l'hôte
        info(f"{PHASE} -- Ping Host Fail")
        hc_ping(HC_HOST_SLUG_PREFIX, "fail", data=f"UNKNOWN: {PHASE}")
        warn("Unknown Phase", PHASE)

if __name__ == "__main__":
    # If no arguments are provided, display help
    if len(sys.argv) == 1:
        print("Usage: vzdump-hook-script.py [phase] [mode] [vmid] [options]")
        print("Example: vzdump-hook-script.py job-init")
        print("For complete options: vzdump-hook-script.py --help")
        sys.exit(1)
    
    main()