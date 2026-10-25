#!/bin/bash
set -Eeuo pipefail

clear 2>/dev/null || true
echo "============================================================"
echo " Ubuntu Cloud Desktop - live system dashboard"
echo " $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "============================================================"
echo
echo "--- Host ----------------------------------------------------"
hostnamectl 2>/dev/null || true
echo
echo "--- Uptime / load -------------------------------------------"
uptime 2>/dev/null || true
echo
echo "--- Memory --------------------------------------------------"
free -h 2>/dev/null || true
echo
echo "--- Swap ----------------------------------------------------"
swapon --show 2>/dev/null || true
if [ -s /run/desktop-swap-status.json ]; then
    cat /run/desktop-swap-status.json
else
    echo "swap status file not available yet"
fi
echo
echo "--- Disk ----------------------------------------------------"
df -h / /var/swap 2>/dev/null || df -h / 2>/dev/null || true
echo
echo "--- Supervisor services -------------------------------------"
supervisorctl status 2>/dev/null || true
echo
echo "--- KasmVNC -------------------------------------------------"
pgrep -a Xvnc 2>/dev/null || true
echo
echo "--- Top processes -------------------------------------------"
ps -eo pid,comm,%cpu,%mem --sort=-%cpu 2>/dev/null | head -12 || true
echo
echo "============================================================"
