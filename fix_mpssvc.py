import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import os
import paramiko
import time

HOST = '88.99.142.89'
USER = 'root'
def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

PASSWORD = require_env("GIFMAKE_RESCUE_PASSWORD")

def run_cmd(ssh, cmd, timeout=60):
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print("STDOUT:", out)
    if err.strip():
        print("STDERR:", err)
    return out, err

def main():
    print("Connecting to Hetzner rescue system...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False, allow_agent=False)
    print("Connected.")

    # Step 1: Mount Windows partition
    print("\n=== Step 1: Mount Windows partition ===")
    run_cmd(ssh, "mount | grep /mnt/win || (mkdir -p /mnt/win && mount -t ntfs-3g -o rw,big_writes,remove_hiberfile /dev/sdb2 /mnt/win && echo 'Mounted successfully')")

    # Verify mount
    out, _ = run_cmd(ssh, "mount | grep /mnt/win")
    if '/mnt/win' not in out:
        print("ERROR: Mount failed, aborting.")
        ssh.close()
        return

    # Step 2: Create .reg file for MpsSvc ControlSet001
    print("\n=== Step 2: Create .reg file for MpsSvc + BFE ===")
    reg_content = """Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\MpsSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\BFE]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet002\\Services\\MpsSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet002\\Services\\BFE]
"Start"=dword:00000004

"""

    # Write the reg file via heredoc
    write_cmd = "cat > /tmp/mps_fix.reg << 'REGEOF'\n" + reg_content + "REGEOF"
    run_cmd(ssh, write_cmd)

    # Verify reg file was written
    run_cmd(ssh, "cat /tmp/mps_fix.reg")

    # Step 3: Apply ControlSet001 changes
    print("\n=== Step 3: Apply reg changes to ControlSet001 (MpsSvc + BFE) ===")
    out, err = run_cmd(ssh,
        "reged -I /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\\SYSTEM' /tmp/mps_fix.reg 2>&1",
        timeout=120
    )
    print("reged output (combined):", out, err)

    # Step 4: Verify MpsSvc ControlSet001
    print("\n=== Step 4: Verify MpsSvc ControlSet001 ===")
    run_cmd(ssh,
        "reged -x /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\\SYSTEM' "
        "'\\ControlSet001\\Services\\MpsSvc' /tmp/mps_verify.txt 2>&1 && cat /tmp/mps_verify.txt",
        timeout=60
    )

    # Step 5: Verify BFE ControlSet001
    print("\n=== Step 5: Verify BFE ControlSet001 ===")
    run_cmd(ssh,
        "reged -x /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\\SYSTEM' "
        "'\\ControlSet001\\Services\\BFE' /tmp/bfe_verify.txt 2>&1 && cat /tmp/bfe_verify.txt",
        timeout=60
    )

    # Step 6: Verify MpsSvc ControlSet002
    print("\n=== Step 6: Verify MpsSvc ControlSet002 ===")
    run_cmd(ssh,
        "reged -x /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\\SYSTEM' "
        "'\\ControlSet002\\Services\\MpsSvc' /tmp/mps2_verify.txt 2>&1 && cat /tmp/mps2_verify.txt",
        timeout=60
    )

    # Step 7: Verify BFE ControlSet002
    print("\n=== Step 7: Verify BFE ControlSet002 ===")
    run_cmd(ssh,
        "reged -x /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\\SYSTEM' "
        "'\\ControlSet002\\Services\\BFE' /tmp/bfe2_verify.txt 2>&1 && cat /tmp/bfe2_verify.txt",
        timeout=60
    )

    print("\n=== Done. Mount left intact as requested. ===")
    ssh.close()

if __name__ == '__main__':
    main()
