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

def run_cmd(ssh, cmd, timeout=60):
    print(f"\n>>> {cmd[:120]}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out:
        print(out[:3000])
    if err and err.strip():
        print(f"[STDERR] {err[:1000]}")
    return out, err

def connect():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD,
                look_for_keys=False, allow_agent=False, timeout=30)
    print(f"Connected to {HOST}")
    return ssh

def mount_windows(ssh):
    out, err = run_cmd(ssh, "mount | grep /mnt/win")
    if "/mnt/win" in out:
        print("Already mounted.")
        return
    print("Mounting Windows partition...")
    out, err = run_cmd(ssh,
        "mkdir -p /mnt/win && mount -t ntfs-3g -o rw,big_writes,remove_hiberfile /dev/sdb2 /mnt/win",
        timeout=30)
    out2, _ = run_cmd(ssh, "mount | grep /mnt/win")
    if "/mnt/win" not in out2:
        raise RuntimeError("Failed to mount /dev/sdb2 to /mnt/win")
    print("Mounted successfully.")

SYSTEM_HIVE = "/mnt/win/Windows/System32/config/SYSTEM"

def reged_export(ssh, key_path):
    """Export a registry key using reged and return the output."""
    cmd = f'reged -x {SYSTEM_HIVE} "HKEY_LOCAL_MACHINE\\SYSTEM" "{key_path}" /dev/stdout 2>/dev/null'
    out, err = run_cmd(ssh, cmd, timeout=30)
    return out

def parse_reg_value(reg_output, value_name):
    """
    Parse a specific value from reged -x output (Windows .reg format).
    Returns (type, data) tuple or None if not found.
    """
    for line in reg_output.splitlines():
        line = line.strip()
        # Handle quoted value names like: "fDenyTSConnections"=dword:00000000
        if line.startswith(f'"{value_name}"='):
            rest = line.split('=', 1)[1]
            if ':' in rest:
                typ, data = rest.split(':', 1)
                return typ.strip(), data.strip()
            else:
                # String value without type prefix
                return 'sz', rest.strip('"')
    return None

def dword_val(data_str):
    """Convert dword hex string like 00000000 to int."""
    return int(data_str, 16)

def check_and_fix(ssh):
    print("\n" + "="*60)
    print("STEP 1: Exporting registry keys via reged")
    print("="*60)

    # Key paths to export
    ts_key     = "\\ControlSet001\\Control\\Terminal Server"
    rdptcp_key = "\\ControlSet001\\Control\\Terminal Server\\WinStations\\RDP-Tcp"
    term_key   = "\\ControlSet001\\Services\\TermService"
    umrdp_key  = "\\ControlSet001\\Services\\UmRdpService"
    sess_key   = "\\ControlSet001\\Services\\SessionEnv"

    ts_out     = reged_export(ssh, ts_key)
    rdptcp_out = reged_export(ssh, rdptcp_key)
    term_out   = reged_export(ssh, term_key)
    umrdp_out  = reged_export(ssh, umrdp_key)
    sess_out   = reged_export(ssh, sess_key)

    print("\n" + "="*60)
    print("STEP 2: Checking values")
    print("="*60)

    # Expected values: (output, key_path, value_name, expected_int)
    checks = [
        (ts_out,     ts_key,     "fDenyTSConnections", 0),
        (ts_out,     ts_key,     "AllowRemoteRPC",     1),
        (rdptcp_out, rdptcp_key, "UserAuthentication", 0),
        (rdptcp_out, rdptcp_key, "SecurityLayer",      0),
        (rdptcp_out, rdptcp_key, "fEnableWinStation",  1),
        (rdptcp_out, rdptcp_key, "PortNumber",         3389),
        (term_out,   term_key,   "Start",              2),
        (umrdp_out,  umrdp_key,  "Start",              2),
        (sess_out,   sess_key,   "Start",              2),
    ]

    correct = []
    needs_fix = []

    for out, key_path, val_name, expected in checks:
        result = parse_reg_value(out, val_name)
        if result is None:
            print(f"  MISSING  {key_path}\\{val_name}  (expected {expected})")
            needs_fix.append((key_path, val_name, expected, True))  # True = new key
        else:
            typ, data = result
            actual = dword_val(data) if 'dword' in typ.lower() else data
            if actual == expected:
                print(f"  OK       {key_path}\\{val_name} = {actual}")
                correct.append((key_path, val_name, actual))
            else:
                print(f"  WRONG    {key_path}\\{val_name} = {actual}  (expected {expected})")
                needs_fix.append((key_path, val_name, expected, False))  # False = existing

    return correct, needs_fix

def fix_values(ssh, needs_fix):
    if not needs_fix:
        print("\nAll values correct. Nothing to fix.")
        return

    print("\n" + "="*60)
    print(f"STEP 3: Fixing {len(needs_fix)} incorrect/missing value(s)")
    print("="*60)

    for key_path, val_name, expected_val, is_new in needs_fix:
        hex_val = format(expected_val, '08x')
        # Use reged -I to write. For new keys use -C; for existing keys omit -C.
        # reged -I <hive> <root> <key_path> <value_name> REG_DWORD <data>
        # Actually reged -I reads from stdin a .reg format
        # Build a mini .reg file content and pipe it
        reg_content = (
            "Windows Registry Editor Version 5.00\r\n"
            "\r\n"
            f'[HKEY_LOCAL_MACHINE\\SYSTEM{key_path}]\r\n'
            f'"{val_name}"=dword:{hex_val}\r\n'
            "\r\n"
        )
        # Write to a temp file on the remote, then import with reged -I
        tmp_file = f"/tmp/rdp_fix_{val_name}.reg"
        # Use printf to write the file
        escaped = reg_content.replace("'", "'\\''")
        write_cmd = f"printf '%s' '{escaped}' > {tmp_file}"
        run_cmd(ssh, write_cmd)

        # Import with reged -I (no -C flag = import into existing hive)
        import_cmd = f'reged -I {SYSTEM_HIVE} "HKEY_LOCAL_MACHINE\\SYSTEM" {tmp_file} 2>&1'
        out, err = run_cmd(ssh, import_cmd, timeout=30)

        # Verify the fix
        print(f"  Verifying fix for {key_path}\\{val_name}...")

def verify_after_fix(ssh):
    """Re-run checks after fixing."""
    print("\n" + "="*60)
    print("STEP 4: Final verification after fixes")
    print("="*60)
    correct, still_wrong = check_and_fix(ssh)
    return correct, still_wrong

def main():
    ssh = connect()
    try:
        mount_windows(ssh)
        correct, needs_fix = check_and_fix(ssh)

        if needs_fix:
            fix_values(ssh, needs_fix)
            # Small delay for hive to flush
            time.sleep(2)
            correct2, still_wrong = verify_after_fix(ssh)

            print("\n" + "="*60)
            print("FINAL REPORT")
            print("="*60)
            print(f"  Values correct from start : {len(correct)}")
            print(f"  Values fixed successfully  : {len(needs_fix) - len(still_wrong)}")
            if still_wrong:
                print(f"  Values STILL WRONG        : {len(still_wrong)}")
                for key_path, val_name, expected, _ in still_wrong:
                    print(f"    - {key_path}\\{val_name} (expected {expected})")
            else:
                print("  All values now correct!")
        else:
            print("\n" + "="*60)
            print("FINAL REPORT")
            print("="*60)
            print(f"  All {len(correct)} values were already correct. No fixes needed.")

    finally:
        ssh.close()
        print("\nSSH connection closed.")

if __name__ == "__main__":
    main()
