import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import os
import paramiko
import time

HOST = "88.99.142.89"
USER = "root"
def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

PASSWORD = require_env("GIFMAKE_RESCUE_PASSWORD")

def run(client, cmd, timeout=60):
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if out:
        print(out)
    if err:
        print(f"[STDERR] {err}")
    return out, err

def main():
    print("=" * 60)
    print("Connecting to Hetzner rescue system...")
    print("=" * 60)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD,
                   look_for_keys=False, allow_agent=False, timeout=30)
    print("Connected.")

    # Step 1: Check if already mounted
    print("\n" + "=" * 60)
    print("STEP 1: Check mount status")
    print("=" * 60)
    run(client, "mount | grep /mnt/win")

    # Step 2: Mount if needed
    print("\n" + "=" * 60)
    print("STEP 2: Mount Windows partition")
    print("=" * 60)
    run(client, "lsblk /dev/sdb")
    run(client, "mkdir -p /mnt/win")
    out, err = run(client, "mount -t ntfs-3g -o ro /dev/sdb2 /mnt/win 2>&1 || mount | grep /mnt/win")
    time.sleep(2)

    # Verify mount
    run(client, "mount | grep /mnt/win")
    run(client, "ls /mnt/win/Windows/ | head -20")

    # Step 3: List event log files
    print("\n" + "=" * 60)
    print("STEP 3: List Windows Event Log files")
    print("=" * 60)
    run(client, "ls -la /mnt/win/Windows/System32/winevt/Logs/ 2>&1 | head -60")

    # Step 4: Check firewall-specific event log
    print("\n" + "=" * 60)
    print("STEP 4: Firewall Event Log")
    print("=" * 60)
    run(client,
        "ls -la '/mnt/win/Windows/System32/winevt/Logs/'*Firewall* 2>&1 || "
        "ls -la '/mnt/win/Windows/System32/winevt/Logs/' | grep -i firewall",
        timeout=30)

    # Step 5: Check python3 availability
    print("\n" + "=" * 60)
    print("STEP 5: Check python3 availability")
    print("=" * 60)
    out, _ = run(client, "which python3 && python3 --version")

    # Step 6: strings on Firewall event log
    print("\n" + "=" * 60)
    print("STEP 6: strings on Firewall event log")
    print("=" * 60)
    run(client,
        "strings '/mnt/win/Windows/System32/winevt/Logs/Microsoft-Windows-Windows Firewall With Advanced Security%4Firewall.evtx' "
        "2>/dev/null | grep -i 'firewall\\|enable\\|disable\\|change\\|policy\\|group' | tail -80",
        timeout=60)

    # Also try the operational log
    run(client,
        "strings '/mnt/win/Windows/System32/winevt/Logs/Microsoft-Windows-Windows Firewall With Advanced Security%4ConnectionSecurity.evtx' "
        "2>/dev/null | grep -i 'firewall\\|enable\\|disable\\|change' | tail -30",
        timeout=30)

    # Step 7: strings on System.evtx for firewall/MpsSvc/BFE
    print("\n" + "=" * 60)
    print("STEP 7: System.evtx - MpsSvc/Firewall/BFE entries")
    print("=" * 60)
    run(client,
        "strings /mnt/win/Windows/System32/winevt/Logs/System.evtx "
        "| grep -i 'MpsSvc\\|firewall\\|BFE\\|Windows Defender\\|mpssvc' | tail -50",
        timeout=60)

    # Step 8: Group Policy logs
    print("\n" + "=" * 60)
    print("STEP 8: Group Policy Operational log")
    print("=" * 60)
    run(client,
        "strings '/mnt/win/Windows/System32/winevt/Logs/Microsoft-Windows-GroupPolicy%4Operational.evtx' "
        "2>/dev/null | grep -i 'firewall\\|policy\\|CSE\\|applied' | tail -50",
        timeout=60)

    # Also raw tail
    run(client,
        "strings '/mnt/win/Windows/System32/winevt/Logs/Microsoft-Windows-GroupPolicy%4Operational.evtx' "
        "2>/dev/null | tail -40",
        timeout=30)

    # Step 9: Windows Update history
    print("\n" + "=" * 60)
    print("STEP 9: Windows Update / SoftwareDistribution")
    print("=" * 60)
    run(client, "ls -la /mnt/win/Windows/SoftwareDistribution/ 2>&1")
    run(client, "ls /mnt/win/Windows/SoftwareDistribution/Download/ 2>/dev/null | wc -l")
    run(client, "ls -lt /mnt/win/Windows/SoftwareDistribution/Download/ 2>/dev/null | head -20")

    # Check Windows Update logs
    run(client,
        "strings /mnt/win/Windows/System32/winevt/Logs/System.evtx "
        "| grep -i 'update\\|wuauserv\\|WindowsUpdate' | tail -20",
        timeout=30)

    # Step 10: pagefile / hiberfil timestamps
    print("\n" + "=" * 60)
    print("STEP 10: pagefile.sys / hiberfil.sys timestamps (last boot)")
    print("=" * 60)
    run(client, "ls -la /mnt/win/pagefile.sys /mnt/win/hiberfil.sys 2>/dev/null")

    # Step 11: Registry check for firewall policy
    print("\n" + "=" * 60)
    print("STEP 11: Registry - Firewall policy keys")
    print("=" * 60)
    # Check if hivex tools are available
    run(client, "which hivexget hivexregedit reglookup 2>/dev/null || echo 'no hivex tools'")

    # Try to find firewall settings in registry hive via strings
    run(client,
        "strings /mnt/win/Windows/System32/config/SOFTWARE "
        "| grep -i 'MpsSvc\\|EnableFirewall\\|firewall\\|BFE' | tail -40",
        timeout=60)

    run(client,
        "strings /mnt/win/Windows/System32/config/SYSTEM "
        "| grep -i 'EnableFirewall\\|MpsSvc\\|BFE\\|firewall' | tail -40",
        timeout=60)

    # Check Task Scheduler for any firewall-related tasks
    print("\n" + "=" * 60)
    print("STEP 12: Task Scheduler XML files - firewall tasks")
    print("=" * 60)
    run(client,
        "grep -r -i 'firewall\\|MpsSvc\\|BFE' "
        "/mnt/win/Windows/System32/Tasks/ 2>/dev/null | head -30",
        timeout=30)

    run(client,
        "ls /mnt/win/Windows/System32/Tasks/ 2>/dev/null | head -30",
        timeout=15)

    # Check for Group Policy firewall settings files
    print("\n" + "=" * 60)
    print("STEP 13: Group Policy firewall config files")
    print("=" * 60)
    run(client,
        "find /mnt/win/Windows/System32/GroupPolicy -name '*.pol' 2>/dev/null | head -20",
        timeout=30)
    run(client,
        "find /mnt/win/ProgramData/Microsoft/Windows/Group Policy -name '*.pol' 2>/dev/null | head -20",
        timeout=30)
    run(client,
        "ls /mnt/win/Windows/System32/GroupPolicy/ 2>/dev/null",
        timeout=15)

    # Check Application.evtx for clues
    print("\n" + "=" * 60)
    print("STEP 14: Application.evtx - relevant entries")
    print("=" * 60)
    run(client,
        "strings /mnt/win/Windows/System32/winevt/Logs/Application.evtx "
        "| grep -i 'firewall\\|MpsSvc\\|BFE\\|security\\|policy\\|restore' | tail -30",
        timeout=60)

    # Check Security.evtx
    print("\n" + "=" * 60)
    print("STEP 15: Security.evtx - firewall policy change events")
    print("=" * 60)
    run(client,
        "strings /mnt/win/Windows/System32/winevt/Logs/Security.evtx "
        "| grep -i 'firewall\\|policy\\|changed\\|rule' | tail -30",
        timeout=60)

    # Check Windows Defender logs
    print("\n" + "=" * 60)
    print("STEP 16: Windows Defender / Security Center logs")
    print("=" * 60)
    run(client,
        "strings '/mnt/win/Windows/System32/winevt/Logs/Microsoft-Windows-Security-Mitigations%4KernelMode.evtx' "
        "2>/dev/null | tail -20",
        timeout=30)
    run(client,
        "ls '/mnt/win/Windows/System32/winevt/Logs/' | grep -i 'defender\\|security\\|mpssvc\\|BFE' ",
        timeout=15)

    print("\n" + "=" * 60)
    print("Investigation complete. NOT unmounting as requested.")
    print("=" * 60)

    client.close()

if __name__ == "__main__":
    main()
