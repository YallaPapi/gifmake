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
WIN_PARTITION = "/dev/sdb2"
MOUNT_POINT = "/mnt/win"

def run(ssh, cmd, timeout=60):
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    rc = stdout.channel.recv_exit_status()
    if out:
        print(f"STDOUT: {out}")
    if err:
        print(f"STDERR: {err}")
    print(f"RC: {rc}")
    return rc, out, err

def main():
    print("=== Connecting to Hetzner rescue system ===")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False)
    print("Connected.")

    # --- Step 1: Check / mount Windows partition ---
    print("\n=== Step 1: Mount Windows partition ===")
    rc, out, err = run(ssh, f"mountpoint -q {MOUNT_POINT} && echo MOUNTED || echo NOT_MOUNTED")
    if "NOT_MOUNTED" in out:
        run(ssh, f"mkdir -p {MOUNT_POINT}")
        rc, out, err = run(ssh, f"mount -t ntfs-3g {WIN_PARTITION} {MOUNT_POINT} -o remove_hiberfile")
        if rc != 0:
            # Try without remove_hiberfile
            rc, out, err = run(ssh, f"mount -t ntfs-3g {WIN_PARTITION} {MOUNT_POINT}")
        if rc != 0:
            print("ERROR: Could not mount Windows partition. Aborting.")
            ssh.close()
            return
        print("Partition mounted successfully.")
    else:
        print("Partition already mounted.")

    # Confirm hive exists
    rc, out, err = run(ssh, f"ls {MOUNT_POINT}/Windows/System32/config/SOFTWARE")
    if rc != 0:
        print("ERROR: SOFTWARE hive not found. Wrong partition?")
        ssh.close()
        return

    # --- Step 2: Write defender.reg ---
    print("\n=== Step 2: Write Defender Group Policy registry file ===")
    defender_reg = r"""Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender]
"DisableAntiSpyware"=dword:00000001
"DisableAntiVirus"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection]
"DisableRealtimeMonitoring"=dword:00000001
"DisableBehaviorMonitoring"=dword:00000001
"DisableOnAccessProtection"=dword:00000001
"DisableScanOnRealtimeEnable"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender Security Center\Notifications]
"DisableNotifications"=dword:00000001
"""

    # Write via heredoc
    cmd = f"cat > /tmp/defender.reg << 'REGEOF'\n{defender_reg}\nREGEOF"
    rc, out, err = run(ssh, cmd)
    run(ssh, "cat /tmp/defender.reg")

    # --- Step 3: Apply Defender policy with -C (create missing keys) ---
    print("\n=== Step 3: Apply Defender Group Policy (reged -C) ===")
    rc, out, err = run(ssh, (
        f"reged -I -C "
        f"{MOUNT_POINT}/Windows/System32/config/SOFTWARE "
        f"'HKEY_LOCAL_MACHINE\\SOFTWARE' "
        f"/tmp/defender.reg 2>&1"
    ), timeout=120)
    if rc != 0:
        print(f"WARNING: reged returned rc={rc} — may still have applied partial changes.")

    # --- Step 4: Write security center / error reporting reg ---
    print("\n=== Step 4: Write Security Center / Error Reporting registry file ===")
    security_reg = r"""Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Windows Error Reporting]
"Disabled"=dword:00000001

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer]
"HideSCAHealth"=dword:00000001
"""
    cmd = f"cat > /tmp/security.reg << 'REGEOF'\n{security_reg}\nREGEOF"
    run(ssh, cmd)
    run(ssh, "cat /tmp/security.reg")

    print("\n=== Step 4b: Apply Security Center settings (reged -C) ===")
    rc, out, err = run(ssh, (
        f"reged -I -C "
        f"{MOUNT_POINT}/Windows/System32/config/SOFTWARE "
        f"'HKEY_LOCAL_MACHINE\\SOFTWARE' "
        f"/tmp/security.reg 2>&1"
    ), timeout=120)
    if rc != 0:
        print(f"WARNING: reged returned rc={rc}")

    # --- Step 5: Disable Tamper Protection ---
    print("\n=== Step 5: Disable Tamper Protection ===")
    tamper_reg = r"""Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows Defender\Features]
"TamperProtection"=dword:00000004
"""
    cmd = f"cat > /tmp/tamper.reg << 'REGEOF'\n{tamper_reg}\nREGEOF"
    run(ssh, cmd)
    run(ssh, "cat /tmp/tamper.reg")

    print("\n=== Step 5b: Apply Tamper Protection (reged -C) ===")
    rc, out, err = run(ssh, (
        f"reged -I -C "
        f"{MOUNT_POINT}/Windows/System32/config/SOFTWARE "
        f"'HKEY_LOCAL_MACHINE\\SOFTWARE' "
        f"/tmp/tamper.reg 2>&1"
    ), timeout=120)
    if rc != 0:
        print(f"WARNING: reged returned rc={rc}")

    # --- Step 6: Verify by reading back keys ---
    print("\n=== Step 6: Verify applied settings (reged -x export) ===")

    keys_to_verify = [
        ("Defender Policy",
         r"HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender",
         "/tmp/verify_defender.reg"),
        ("Defender Real-Time Protection",
         r"HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection",
         "/tmp/verify_rtp.reg"),
        ("Defender Features (Tamper)",
         r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows Defender\Features",
         "/tmp/verify_features.reg"),
        ("Security Center / Explorer Policies",
         r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer",
         "/tmp/verify_explorer.reg"),
        ("Error Reporting",
         r"HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\Windows Error Reporting",
         "/tmp/verify_wer.reg"),
    ]

    results = {}
    for label, key, outfile in keys_to_verify:
        print(f"\n--- Verifying: {label} ---")
        rc, out, err = run(ssh, (
            f"reged -x "
            f"{MOUNT_POINT}/Windows/System32/config/SOFTWARE "
            f"'{key}' "
            f"{outfile} 2>&1 && cat {outfile}"
        ), timeout=60)
        results[label] = (rc, out)

    # --- Summary ---
    print("\n" + "="*60)
    print("SUMMARY OF APPLIED POLICIES")
    print("="*60)

    policy_map = {
        "DisableAntiSpyware":           ("Defender Policy",                  "dword:00000001"),
        "DisableAntiVirus":             ("Defender Policy",                  "dword:00000001"),
        "DisableRealtimeMonitoring":    ("Defender Real-Time Protection",     "dword:00000001"),
        "DisableBehaviorMonitoring":    ("Defender Real-Time Protection",     "dword:00000001"),
        "DisableOnAccessProtection":    ("Defender Real-Time Protection",     "dword:00000001"),
        "DisableScanOnRealtimeEnable":  ("Defender Real-Time Protection",     "dword:00000001"),
        "TamperProtection":             ("Defender Features (Tamper)",        "dword:00000004"),
        "HideSCAHealth":               ("Security Center / Explorer Policies","dword:00000001"),
        "Disabled":                    ("Error Reporting",                   "dword:00000001"),
    }

    applied = []
    missing = []

    for value_name, (label, expected) in policy_map.items():
        content = results.get(label, (1, ""))[1]
        if value_name in content and expected in content:
            applied.append(f"  [OK] {value_name} = {expected}  ({label})")
        else:
            missing.append(f"  [??] {value_name}  ({label}) — not confirmed in export")

    for line in applied:
        print(line)
    for line in missing:
        print(line)

    print(f"\nApplied/confirmed: {len(applied)}")
    print(f"Not confirmed:     {len(missing)}")
    print("\nNOTE: Partition left mounted at /mnt/win as requested.")
    print("NOTE: Changes take effect on next Windows boot.")

    ssh.close()
    print("\nSSH connection closed.")

if __name__ == "__main__":
    main()
