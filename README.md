# Proxmox VZDump Backup Hook with Healthchecks Integration

This project provides a Python script that integrates Proxmox VZDump backups with [Healthchecks.io](https://healthchecks.io) or a self-hosted instance for monitoring and alerting.

## Overview

The script runs at various phases of the Proxmox backup task to monitor and report backup status to Healthchecks.io (or self-hosted). It creates check endpoints for each VM/container and for the overall backup job, allowing you to track the progress and success of your backups.

## Features

- Monitors all phases of the Proxmox backup process
- Creates and updates Healthchecks.io endpoints automatically
- Sends real-time status updates during the backup process
- Captures and forwards backup logs to Healthchecks.io
- Works with both standalone Proxmox servers and clusters
- Supports multiple backup jobs

## Prerequisites

- Proxmox VE 6.x or later
- Python 3.6 or later (included with Proxmox)
- Python `requests` library
- `jq` command-line JSON processor
- Healthchecks.io account (self-hosted or cloud)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/proxmox-healthchecks-hook.git
cd proxmox-healthchecks-hook
```

### 2. Install dependencies

```bash
apt-get update
apt-get install -y python3-requests jq
```

### 3. Create directories

```bash
mkdir -p /etc/pve/healthchecks
```

### 4. Copy the script to the executable location

```bash
cp vzdump-hook-script.py /usr/local/bin/
chmod +x /usr/local/bin/vzdump-hook-script.py
```

### 5. Configure environment variables

Create the environment file:

```bash
cp variables.env.example /etc/pve/healthchecks/variables.env
```

Edit the file with your Healthchecks.io details:

```bash
nano /etc/pve/healthchecks/variables.env
```

Example configuration:

```
# Healthchecks.io Configuration for Proxmox VZDump Backup Hook Script
# This file is loaded by the vzdump-hook-script.py script
# This file should be located at /etc/pve/healthchecks/variables.env
# Since it's in /etc/pve, it will be synchronized across the Proxmox cluster

# Base domain for Healthchecks API
HC_BASE_DOMAIN=https://<your hosted healthchecks>

# Domain for Healthchecks ping endpoints
HC_PING_DOMAIN=https://<your hosted healthchecks>/ping

# API key with read-write permissions
HC_RW_API_KEY=<your project read-write api key>

# Ping key used for sending pings
HC_PING_KEY=<your project ping key>
```

### 6. Add the script to your backup jobs

Run this command to automatically add the hook script to all existing VZDump backup jobs:

```bash
awk 'BEGIN{job=0;has_script=0}/^vzdump:/{if(job&&!has_script)print"        script /usr/local/bin/vzdump-hook-script.py";job=1;has_script=0;print;next}/script \/usr\/local\/bin\/vzdump-hook-script.py/{has_script=1;print;next}/^[a-zA-Z]/{if(job&&$0!~/^[[:space:]]/) {if(!has_script)print"        script /usr/local/bin/vzdump-hook-script.py";job=0;has_script=0}print;next}{print}END{if(job&&!has_script)print"        script /usr/local/bin/vzdump-hook-script.py"}' /etc/pve/jobs.cfg > /tmp/jobs.cfg.new && sudo cp /tmp/jobs.cfg.new /etc/pve/jobs.cfg
```

### 7. Verify installation

Test the script with:

```bash
/usr/local/bin/vzdump-hook-script.py job-init
```

You should see a new check created in your Healthchecks.io dashboard.

## Cluster Installation

If you're running a Proxmox cluster:

1. Install the script on each node in the cluster:
   ```bash
   cp vzdump-hook-script.py /usr/local/bin/
   chmod +x /usr/local/bin/vzdump-hook-script.py
   ```

2. The configuration file in `/etc/pve/healthchecks/variables.env` will be automatically synchronized across all nodes in the cluster.

3. Run the awk command on one of the nodes to update all backup jobs.

## How It Works

The script hooks into the following phases of the Proxmox backup process:

- `job-init`: Creates the Healthchecks endpoint for the host
- `job-start`: Sends a start ping to the host endpoint
- `backup-start`: Creates and starts the Healthchecks endpoint for the VM/container
- `pre-stop`, `pre-restart`, `post-restart`: Logs progress during VM/container snapshots
- `backup-end`: Sends a success ping when a VM/container backup completes
- `log-end`: Sends VM/container logs to Healthchecks
- `job-end`: Sends a success ping when the entire backup job completes

When issues occur, the script captures errors and reports them to Healthchecks.io with detailed logs.

## Customization

You can customize the script behavior by modifying the variables in `/etc/pve/healthchecks/variables.env`. The most important options are:

- `HC_BASE_DOMAIN`: Your Healthchecks instance base URL
- `HC_PING_DOMAIN`: URL for sending pings (usually `HC_BASE_DOMAIN/ping`)
- `HC_RW_API_KEY`: API key with read-write permissions
- `HC_PING_KEY`: Key used for sending pings

## Upgrading

To upgrade to a newer version:

1. Replace the script on each node:
   ```bash
   cp vzdump-hook-script.py /usr/local/bin/
   chmod +x /usr/local/bin/vzdump-hook-script.py
   ```

2. Update the configuration file if needed:
   ```bash
   cp variables.env.example /etc/pve/healthchecks/variables.env
   # Edit with your settings
   ```

## Troubleshooting

If you encounter issues:

1. Check the script permissions: `chmod +x /usr/local/bin/vzdump-hook-script.py`
2. Verify Python and requests are installed: `python3 -c "import requests"`
3. Check for errors in the Proxmox task logs
4. Manually run the script with different phases to test
5. Ensure your Healthchecks API keys have the correct permissions

## Author & Changelog

Author: Cédric MARCOUX  
Localisation: Aywaille, Belgium  
Version: 1.0.0

Release Notes:
- **1.0.0 (2025-03-12)**:
  - Initial release

## License

[MIT License](LICENSE)

## Acknowledgments

- [Proxmox Team](https://www.proxmox.com/) for the VZDump backup tool
- [Healthchecks.io](https://healthchecks.io) for the monitoring service
- Original script based on [waza-ari's work](https://gist.github.com/waza-ari/8fb8375ec5770a50486abeb2a7bb9c52)