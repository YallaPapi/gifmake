import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import os
import paramiko

HOST = "88.99.142.89"
USER = "root"
def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

PASSWORD = require_env("GIFMAKE_RESCUE_PASSWORD")
MOUNT_POINT = "/mnt/win"
HIVE = f"{MOUNT_POINT}/Windows/System32/config/SOFTWARE"

def run(ssh, cmd, timeout=60):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    rc = stdout.channel.recv_exit_status()
    return rc, out, err

def verify(ssh, label, key_subpath, outfile):
    """
    reged -x syntax (correct):
      reged -x <hive> 'HKEY_LOCAL_MACHINE\SOFTWARE' '\SubKey\Path' output.reg
    The key must start with a single backslash, relative to the hive prefix.
    """
    cmd = (
        f"reged -x '{HIVE}' 'HKEY_LOCAL_MACHINE\\SOFTWARE' "
        f"'{key_subpath}' {outfile} 2>&1 && cat {outfile}"
    )
    rc, out, err = run(ssh, cmd, timeout=60)
    return rc, out

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False)
    print("Connected. Verifying all applied registry changes...\n")

    verifications = [
        (
            "Defender Group Policy (DisableAntiSpyware, DisableAntiVirus, RTP settings)",
            r"\Policies\Microsoft\Windows Defender",
            "/tmp/v_defender.reg",
            ["DisableAntiSpyware", "DisableAntiVirus",
             "DisableRealtimeMonitoring", "DisableBehaviorMonitoring",
             "DisableOnAccessProtection", "DisableScanOnRealtimeEnable"],
        ),
        (
            "Defender Features (TamperProtection)",
            r"\Microsoft\Windows Defender\Features",
            "/tmp/v_features.reg",
            ["TamperProtection"],
        ),
        (
            "Explorer Policies (HideSCAHealth)",
            r"\Microsoft\Windows\CurrentVersion\Policies\Explorer",
            "/tmp/v_explorer.reg",
            ["HideSCAHealth"],
        ),
        (
            "Windows Error Reporting (Disabled)",
            r"\Policies\Microsoft\Windows\Windows Error Reporting",
            "/tmp/v_wer.reg",
            ["Disabled"],
        ),
    ]

    all_confirmed = []
    all_missing = []

    for label, subpath, outfile, expected_values in verifications:
        print(f"--- {label} ---")
        rc, content = verify(ssh, label, subpath, outfile)
        if rc != 0 and "Exporting" not in content:
            print(f"  ERROR exporting: {content[:200]}")
            for v in expected_values:
                all_missing.append(f"{v}  [{label}]")
            print()
            continue

        print(content)
        print()

        for val in expected_values:
            if f'"{val}"=dword:00000001' in content or f'"{val}"=dword:00000004' in content:
                all_confirmed.append(f"{val}  [{label}]")
            else:
                all_missing.append(f"{val}  [{label}]")

    print("=" * 60)
    print("FINAL VERIFICATION SUMMARY")
    print("=" * 60)
    print(f"\nCONFIRMED ({len(all_confirmed)}):")
    for item in all_confirmed:
        print(f"  [OK]  {item}")

    if all_missing:
        print(f"\nNOT CONFIRMED ({len(all_missing)}):")
        for item in all_missing:
            print(f"  [??]  {item}")
    else:
        print("\nAll policies confirmed in hive.")

    print("\nPartition remains mounted at /mnt/win.")
    print("Changes take effect on next Windows boot.")

    ssh.close()

if __name__ == "__main__":
    main()
