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

def run_cmd(ssh, cmd, timeout=90, stdin_data=None):
    print(f"\n>>> {cmd[:200]}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    if stdin_data:
        stdin.write(stdin_data)
        stdin.channel.shutdown_write()
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print("STDOUT:", out[:3000])
    if err.strip():
        print("STDERR:", err[:3000])
    return out, err

def main():
    print("Connecting to Hetzner rescue system...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False, allow_agent=False)
    print("Connected.")

    # Verify mount
    out, _ = run_cmd(ssh, "mount | grep /mnt/win")
    if '/mnt/win' not in out:
        run_cmd(ssh, "mkdir -p /mnt/win && mount -t ntfs-3g -o rw,big_writes,remove_hiberfile /dev/sdb2 /mnt/win")

    # Step 1: Try to install python3-hivex
    print("\n=== Step 1: Install python3-hivex ===")
    out, err = run_cmd(ssh, "apt-get install -y python3-hivex 2>&1", timeout=120)

    out_check, _ = run_cmd(ssh, "python3 -c 'import hivex; print(\"hivex OK\")' 2>&1")

    if 'hivex OK' in out_check:
        print("\n=== python3-hivex installed, using Python hivex approach ===")

        py_script = '''#!/usr/bin/env python3
import sys, struct, hivex

HIVE = "/mnt/win/Windows/System32/config/SYSTEM"

TARGETS = [
    ("ControlSet001\\\\Services\\\\MpsSvc", "Start", 4),
    ("ControlSet001\\\\Services\\\\BFE",    "Start", 4),
    ("ControlSet002\\\\Services\\\\MpsSvc", "Start", 4),
    ("ControlSet002\\\\Services\\\\BFE",    "Start", 4),
]

h = hivex.Hivex(HIVE, write=True)
root = h.root()

def navigate(h, root, path):
    parts = path.strip("\\\\").split("\\\\")
    node = root
    for part in parts:
        found = None
        for c in h.node_children(node):
            if h.node_name(c) == part:
                found = c
                break
        if found is None:
            print(f"  ERROR: subkey {part!r} not found!")
            return None
        node = found
    return node

results = []
for (path, valname, newval) in TARGETS:
    print(f"Processing: {path}")
    node = navigate(h, root, path)
    if node is None:
        results.append((path, "FAILED - key not found"))
        continue

    # Read current
    for v in h.node_values(node):
        if h.value_key(v) == valname:
            vtype, vdata = h.value_value(v)
            cur = struct.unpack("<I", vdata)[0]
            print(f"  Current Start = {cur}")
            break
    else:
        print(f"  Start value not yet present, will create")

    # Set value
    try:
        new_data = struct.pack("<I", newval)
        h.node_set_value(node, {"key": valname, "t": 4, "value": new_data})
        print(f"  Set Start = {newval} OK")
        results.append((path, f"OK: Start={newval}"))
    except Exception as e:
        print(f"  FAILED: {e}")
        results.append((path, f"FAILED: {e}"))

print("\\nCommitting...")
h.commit(None)
print("Commit done.")

print("\\nSummary:")
for path, status in results:
    print(f"  {path}: {status}")

# Verify
print("\\nVerification:")
h2 = hivex.Hivex(HIVE)
root2 = h2.root()
for (path, valname, _) in TARGETS:
    node = navigate(h2, root2, path)
    if node is None:
        print(f"  {path}: KEY NOT FOUND")
        continue
    for v in h2.node_values(node):
        if h2.value_key(v) == valname:
            vtype, vdata = h2.value_value(v)
            val = struct.unpack("<I", vdata)[0]
            print(f"  {path}: Start={val} ({'DISABLED' if val==4 else 'ACTIVE'})")
            break
    else:
        print(f"  {path}: Start NOT FOUND")
'''
        # Write script to remote
        sftp = ssh.open_sftp()
        with sftp.open('/tmp/fix_hive2.py', 'w') as f:
            f.write(py_script)
        sftp.close()

        run_cmd(ssh, "python3 /tmp/fix_hive2.py", timeout=120)

    else:
        print("\n=== python3-hivex not available, using reged -e with stdin ===")
        print("reged -e supports interactive commands via stdin")

        # reged -e command reference:
        # cd <path>  - change directory
        # setval     - set/create value
        # q          - quit (with save prompt: 'y' to save)
        #
        # reged interactive commands (from reged source):
        # nk <name>    create new key
        # dk <name>    delete key
        # dv <name>    delete value
        # ed           edit value
        # nv <type> <name>  new value
        # The prompt looks like: >

        # We'll use reged -e piped with commands via a shell script using printf
        hive = "/mnt/win/Windows/System32/config/SYSTEM"

        # reged -e interactive mode accepts:
        # ls, cat <val>, cd <subkey>, ed, nv, dv, q
        # For setting: use 'ed' to edit existing DWORD value
        # ed prompts: select value to edit by number, then enter new value

        # First, let's see the actual interactive commands by checking reged source behavior
        # reged -e exits with 'q' then 'y' to save
        # We need to navigate and edit Start values

        # Alternative: use reged with -N flag (no-allocate = only edit same-size values)
        # DWORD is always 4 bytes, so -N is safe for changing Start value

        # The reged -e commands when piped via stdin:
        # It uses readline, which may or may not work with piped stdin
        # Let's try with a heredoc approach through shell

        # Write a shell script that uses printf to pipe commands
        shell_script = r"""#!/bin/bash
HIVE="/mnt/win/Windows/System32/config/SYSTEM"

# Function to run reged and set a DWORD value via interactive mode
# reged interactive prompt is "> "
# Commands: cd <path>, ls, cat <valname>, ed (edit), nv <type> <name>, q (quit)

# We use 'ed' to edit a value. After 'ed', reged shows list of values and asks which to edit
# Then asks for new value.
#
# Actually the safest approach is reged -e with -C -N flags:
# -C = auto-commit (no save prompt)
# -N = no-allocate (only change same-size values, safe for DWORD)

echo "=== Setting MpsSvc Start=4 in ControlSet001 ==="
printf "cd \\ControlSet001\\Services\\MpsSvc\nls\nq\n" | reged -e "$HIVE" 2>&1 | head -30

echo ""
echo "=== Trying direct value set with reged -e ==="
# Check reged's 'ed' behavior
printf "cd \\ControlSet001\\Services\\MpsSvc\ncat Start\nq\n" | reged -e "$HIVE" 2>&1 | head -30
"""
        run_cmd(ssh, "cat > /tmp/test_reged.sh << 'SHEOF'\n" + shell_script + "\nSHEOF && chmod +x /tmp/test_reged.sh")
        run_cmd(ssh, "bash /tmp/test_reged.sh 2>&1", timeout=60)

        # Now try the actual edit with reged -e piping commands
        # reged 'ed' command: shows numbered list of values, you pick a number, then type new value
        # For ControlSet001\Services\MpsSvc, Start is typically value #X
        # We need to know the index. Let's use a different approach:
        # reged supports 'nv' (new value) and has an undocumented 'sv' (set value)?
        # Check reged help inside interactive mode
        run_cmd(ssh, r"""printf "?\nq\n" | reged -e /mnt/win/Windows/System32/config/SYSTEM 2>&1 | head -40""", timeout=30)

        # Try installing hivex tools
        print("\n=== Trying to install hivex tools ===")
        run_cmd(ssh, "apt-get install -y libhivex-bin 2>&1 | tail -5", timeout=120)

        out_hx, _ = run_cmd(ssh, "which hivexregedit hivexsh 2>&1")

        if 'hivexregedit' in out_hx or 'hivexsh' in out_hx:
            print("\n=== hivex tools available! ===")
            # Use hivexregedit to merge .reg file
            reg_content = r"""Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\MpsSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet001\Services\BFE]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet002\Services\MpsSvc]
"Start"=dword:00000004

[HKEY_LOCAL_MACHINE\SYSTEM\ControlSet002\Services\BFE]
"Start"=dword:00000004
"""
            sftp = ssh.open_sftp()
            with sftp.open('/tmp/mps_fix2.reg', 'w') as f:
                f.write(reg_content)
            sftp.close()

            run_cmd(ssh,
                r"hivexregedit --merge /mnt/win/Windows/System32/config/SYSTEM --prefix='HKEY_LOCAL_MACHINE\SYSTEM' /tmp/mps_fix2.reg 2>&1",
                timeout=120
            )

    # Final verification using reged -x export (correct path format without leading backslash)
    print("\n=== Final Verification via reged -x ===")
    # Based on the successful BFE export earlier, the correct syntax is path WITHOUT leading backslash
    run_cmd(ssh,
        r"reged -x /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' 'ControlSet001\Services\MpsSvc' /tmp/mpssvc_final.txt 2>&1 && grep -A2 'MpsSvc\]' /tmp/mpssvc_final.txt | head -20",
        timeout=60
    )
    run_cmd(ssh,
        r"reged -x /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' 'ControlSet001\Services\BFE' /tmp/bfe_final.txt 2>&1 && grep '\"Start\"' /tmp/bfe_final.txt",
        timeout=60
    )
    run_cmd(ssh,
        r"reged -x /mnt/win/Windows/System32/config/SYSTEM 'HKEY_LOCAL_MACHINE\SYSTEM' 'ControlSet002\Services\MpsSvc' /tmp/mpssvc2_final.txt 2>&1 && grep '\"Start\"' /tmp/mpssvc2_final.txt",
        timeout=60
    )

    print("\n=== Done. Mount left intact. ===")
    ssh.close()

if __name__ == '__main__':
    main()
