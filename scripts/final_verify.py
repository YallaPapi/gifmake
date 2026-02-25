import sys
import time
import os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

print("=== FINAL REGISTRY VERIFICATION AGENT ===")
print("Waiting 120 seconds for other agents to finish...")
sys.stdout.flush()
time.sleep(120)
print("Wait complete. Connecting to Hetzner rescue system...")
sys.stdout.flush()

import paramiko

HOST = "88.99.142.89"
USER = "root"
def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

PASSWORD = require_env("GIFMAKE_RESCUE_PASSWORD")
PARTITION = "/dev/sdb2"
MOUNTPOINT = "/mnt/win"
SYSTEM_HIVE  = f"{MOUNTPOINT}/Windows/System32/config/SYSTEM"
SOFTWARE_HIVE = f"{MOUNTPOINT}/Windows/System32/config/SOFTWARE"

def run(client, cmd, timeout=30):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    rc = stdout.channel.recv_exit_status()
    return rc, out, err

def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False, allow_agent=False)
    return client

def hivexget_dword(client, hive, key_path, value_name):
    """Read a DWORD value via hivexget. Returns int or None."""
    cmd = f'hivexget "{hive}" "\\{key_path}" "{value_name}" 2>/dev/null'
    rc, out, err = run(client, cmd, timeout=15)
    if rc == 0 and out.strip():
        try:
            return int(out.strip())
        except ValueError:
            return out.strip()
    return None

def hivexget_sz(client, hive, key_path, value_name):
    """Read a string (SZ) value via hivexget. Returns string or None."""
    cmd = f'hivexget "{hive}" "\\{key_path}" "{value_name}" 2>/dev/null'
    rc, out, err = run(client, cmd, timeout=15)
    if rc == 0:
        return out.strip()
    return None

def hivexget_list_values(client, hive, key_path):
    """List all values in a key. Returns list of (name,val) tuples."""
    cmd = f'hivexget "{hive}" "\\{key_path}" 2>/dev/null'
    rc, out, err = run(client, cmd, timeout=15)
    if rc == 0 and out.strip():
        lines = []
        for line in out.strip().splitlines():
            if "=" in line:
                name, _, val = line.partition("=")
                lines.append((name.strip().strip('"'), val.strip()))
        return lines
    return []

print("Connecting via SSH...")
client = connect()
print("Connected.")

# --- Mount if needed ---
rc, out, err = run(client, f"mountpoint -q {MOUNTPOINT} && echo MOUNTED || echo NOT_MOUNTED")
if "NOT_MOUNTED" in out:
    print(f"Mounting {PARTITION} to {MOUNTPOINT}...")
    run(client, f"mkdir -p {MOUNTPOINT}")
    rc, out, err = run(client, f"mount -t ntfs-3g -o remove_hiberfile {PARTITION} {MOUNTPOINT}")
    if rc != 0:
        rc, out, err = run(client, f"mount -t ntfs-3g {PARTITION} {MOUNTPOINT}")
    if rc != 0:
        print(f"ERROR: Mount failed: {err}")
        sys.exit(1)
    print("Mounted successfully.")
else:
    print("Partition already mounted.")

issues = []
results = []

def check_dword(label, hive, key_path, value_name, expected):
    val = hivexget_dword(client, hive, key_path, value_name)
    ok = (val == expected)
    status = "OK" if ok else "FAIL"
    msg = f"  [{status}] {label}: expected={expected}, got={val}"
    results.append(msg)
    if not ok:
        issues.append(f"{label}: expected={expected}, got={val}")
    print(msg)
    return ok

def check_sz(label, hive, key_path, value_name, expected):
    val = hivexget_sz(client, hive, key_path, value_name)
    ok = (val == expected)
    status = "OK" if ok else "FAIL"
    msg = f"  [{status}] {label}: expected={expected!r}, got={val!r}"
    results.append(msg)
    if not ok:
        issues.append(f"{label}: expected={expected!r}, got={val!r}")
    print(msg)
    return ok

def check_value_exists(label, hive, key_path, value_name):
    val = hivexget_sz(client, hive, key_path, value_name)
    ok = val is not None
    status = "OK" if ok else "FAIL"
    msg = f"  [{status}] {label}: {'found=' + repr(val) if ok else 'NOT FOUND'}"
    results.append(msg)
    if not ok:
        issues.append(f"{label}: not found")
    print(msg)
    return ok

def check_key_has_entries(label, hive, key_path):
    entries = hivexget_list_values(client, hive, key_path)
    if entries:
        msg = f"  [OK] {label}: {len(entries)} entries found"
        for name, val in entries:
            msg += f"\n       {name} = {val}"
    else:
        msg = f"  [WARN] {label}: no entries (RunOnce may have already been consumed)"
    results.append(msg)
    print(msg)
    return len(entries) > 0

# -----------------------------------------------------------------------
print("\n" + "="*60)
print("SYSTEM HIVE CHECKS")
print("="*60)

# Firewall
FW_PROFILES_BASE = "ControlSet001\\Services\\SharedAccess\\Parameters\\FirewallPolicy"
check_dword("FW DomainProfile\\EnableFirewall",   SYSTEM_HIVE, f"{FW_PROFILES_BASE}\\DomainProfile",   "EnableFirewall", 0)
check_dword("FW PublicProfile\\EnableFirewall",   SYSTEM_HIVE, f"{FW_PROFILES_BASE}\\PublicProfile",   "EnableFirewall", 0)
check_dword("FW StandardProfile\\EnableFirewall", SYSTEM_HIVE, f"{FW_PROFILES_BASE}\\StandardProfile", "EnableFirewall", 0)

# MpsSvc
check_dword("MpsSvc\\Start", SYSTEM_HIVE, "ControlSet001\\Services\\MpsSvc", "Start", 4)

# Terminal Server
check_dword("Terminal Server\\fDenyTSConnections", SYSTEM_HIVE, "ControlSet001\\Control\\Terminal Server", "fDenyTSConnections", 0)

# RDP-Tcp
RDP_TCP = "ControlSet001\\Control\\Terminal Server\\WinStations\\RDP-Tcp"
check_dword("RDP-Tcp\\UserAuthentication", SYSTEM_HIVE, RDP_TCP, "UserAuthentication", 0)
check_dword("RDP-Tcp\\SecurityLayer",      SYSTEM_HIVE, RDP_TCP, "SecurityLayer",      0)

# TermService
check_dword("TermService\\Start", SYSTEM_HIVE, "ControlSet001\\Services\\TermService", "Start", 2)

# -----------------------------------------------------------------------
print("\n" + "="*60)
print("SOFTWARE HIVE CHECKS")
print("="*60)

# WindowsFirewall policies
SW_FW = "Policies\\Microsoft\\WindowsFirewall"
check_dword("SW FW DomainProfile\\EnableFirewall",  SOFTWARE_HIVE, f"{SW_FW}\\DomainProfile",  "EnableFirewall", 0)
check_dword("SW FW PublicProfile\\EnableFirewall",  SOFTWARE_HIVE, f"{SW_FW}\\PublicProfile",  "EnableFirewall", 0)
check_dword("SW FW PrivateProfile\\EnableFirewall", SOFTWARE_HIVE, f"{SW_FW}\\PrivateProfile", "EnableFirewall", 0)

# Terminal Services policy
check_dword("SW TS fDenyTSConnections", SOFTWARE_HIVE,
            "Policies\\Microsoft\\Windows NT\\Terminal Services", "fDenyTSConnections", 0)

# Winlogon AutoAdminLogon
check_sz("Winlogon\\AutoAdminLogon", SOFTWARE_HIVE,
         "Microsoft\\Windows NT\\CurrentVersion\\Winlogon", "AutoAdminLogon", "1")

# RunOnce
check_key_has_entries("RunOnce entries",
                      SOFTWARE_HIVE, "Microsoft\\Windows\\CurrentVersion\\RunOnce")

# Run\StartupFix
check_value_exists("Run\\StartupFix",
                   SOFTWARE_HIVE, "Microsoft\\Windows\\CurrentVersion\\Run", "StartupFix")

# -----------------------------------------------------------------------
print("\n" + "="*60)
print("FILE CHECKS")
print("="*60)

for fpath in [
    f"{MOUNTPOINT}/Windows/kill-firewall.bat",
    f"{MOUNTPOINT}/Windows/startup-fix.bat",
]:
    rc, out, err = run(client, f"test -f '{fpath}' && echo EXISTS || echo MISSING")
    exists = "EXISTS" in out
    label = fpath.replace(MOUNTPOINT, "")
    if exists:
        rc2, content, _ = run(client, f"wc -c '{fpath}'; head -5 '{fpath}'")
        msg = f"  [OK] {label} exists\n       {content.strip()}"
    else:
        msg = f"  [FAIL] {label} MISSING"
        issues.append(f"{label} missing")
    results.append(msg)
    print(msg)

# -----------------------------------------------------------------------
print("\n" + "="*60)
print("UNMOUNTING FILESYSTEM")
print("="*60)

print("Running sync...")
rc, out, err = run(client, "sync", timeout=30)
print(f"  sync rc={rc}")

print(f"Unmounting {MOUNTPOINT}...")
rc, out, err = run(client, f"umount {MOUNTPOINT}", timeout=30)
if rc == 0:
    print("  umount OK")
else:
    print(f"  umount rc={rc}, err={err.strip()}")
    rc2, out2, err2 = run(client, f"umount -l {MOUNTPOINT}", timeout=30)
    print(f"  lazy umount rc={rc2}")

print(f"Running ntfsfix -d {PARTITION}...")
rc, out, err = run(client, f"ntfsfix -d {PARTITION}", timeout=30)
print(f"  {out.strip()}")
if err.strip():
    print(f"  stderr: {err.strip()}")

# -----------------------------------------------------------------------
print("\n" + "="*60)
print("FINAL VERDICT")
print("="*60)

warnings = [r for r in results if "[WARN]" in r]

if not issues:
    print("\n  *** GO *** -- All critical checks passed.")
    if warnings:
        print(f"\n  Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    {w.strip()}")
    print("\n  System is ready for reboot.")
    print("  RDP should be accessible at 88.99.142.89:3389 after Windows boots.")
else:
    print(f"\n  *** NO-GO *** -- {len(issues)} critical issue(s) found:\n")
    for i, issue in enumerate(issues, 1):
        print(f"    {i}. {issue}")
    if warnings:
        print(f"\n  Warnings:")
        for w in warnings:
            print(f"    {w.strip()}")
    print("\n  Do NOT reboot until issues are resolved.")

client.close()
print("\n=== DONE ===")
