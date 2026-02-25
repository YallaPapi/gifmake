import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import os
import paramiko

HOST = '88.99.142.89'
USER = 'root'
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
    if out.strip():
        print("STDOUT:", out[:2000])
    if err.strip():
        print("STDERR:", err[:2000])
    return out, err

def main():
    print("Connecting to Hetzner rescue system...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False, allow_agent=False)
    print("Connected.")

    # Verify mount is still up
    print("\n=== Check mount ===")
    out, _ = run_cmd(ssh, "mount | grep /mnt/win")
    if '/mnt/win' not in out:
        print("Re-mounting...")
        run_cmd(ssh, "mkdir -p /mnt/win && mount -t ntfs-3g -o rw,big_writes,remove_hiberfile /dev/sdb2 /mnt/win && echo 'Mounted'")

    # Check what tools are available
    print("\n=== Check available tools ===")
    run_cmd(ssh, "which hivexregedit hivexsh python3 2>&1; python3 -c 'import hivex; print(\"hivex python OK\")' 2>&1")
    run_cmd(ssh, "apt list --installed 2>/dev/null | grep -i hive")
    run_cmd(ssh, "reged --help 2>&1 | head -20")

    # Try hivexregedit if available, else use hivex Python bindings
    # First check if hivexregedit exists
    out_hre, _ = run_cmd(ssh, "which hivexregedit 2>&1")
    out_py, _ = run_cmd(ssh, "python3 -c 'import hivex; print(\"OK\")' 2>&1")

    hive_path = "/mnt/win/Windows/System32/config/SYSTEM"

    if 'OK' in out_py:
        print("\n=== Using Python hivex bindings ===")

        # Write a Python script to the rescue system that uses hivex directly
        py_script = r'''
import sys
import struct
import hivex

HIVE = "/mnt/win/Windows/System32/config/SYSTEM"

# Services to disable (Start=4 means disabled)
TARGETS = [
    ("ControlSet001\\Services\\MpsSvc", "Start", 4),
    ("ControlSet001\\Services\\BFE",    "Start", 4),
    ("ControlSet002\\Services\\MpsSvc", "Start", 4),
    ("ControlSet002\\Services\\BFE",    "Start", 4),
]

h = hivex.Hivex(HIVE, write=True)

root = h.root()

def navigate(h, root, path):
    """Navigate to a node by path like 'ControlSet001\\Services\\MpsSvc'"""
    parts = path.strip("\\").split("\\")
    node = root
    for part in parts:
        children = {h.node_name(c): c for c in h.node_children(node)}
        if part not in children:
            print(f"  ERROR: subkey '{part}' not found under current node!")
            print(f"  Available: {list(children.keys())[:10]}")
            return None
        node = children[part]
    return node

results = []
for (path, valname, newval) in TARGETS:
    print(f"\nProcessing: {path} -> {valname} = {newval}")
    node = navigate(h, root, path)
    if node is None:
        results.append((path, "FAILED - key not found"))
        continue

    # Read current value
    try:
        vals = {h.value_key(v): v for v in h.node_values(node)}
        if valname in vals:
            vtype, vdata = h.value_value(vals[valname])
            cur = struct.unpack("<I", vdata)[0]
            print(f"  Current {valname} = {cur} (type={vtype})")
        else:
            print(f"  Value '{valname}' not found, will create it")
    except Exception as e:
        print(f"  Warning reading current value: {e}")

    # Set new value (dword = type 4)
    try:
        new_data = struct.pack("<I", newval)
        h.node_set_value(node, {
            "key": valname,
            "t": 4,        # REG_DWORD
            "value": new_data
        })
        print(f"  Set {valname} = {newval} OK")
        results.append((path, f"OK: set Start={newval}"))
    except Exception as e:
        print(f"  ERROR setting value: {e}")
        results.append((path, f"FAILED: {e}"))

# Commit changes
print("\n=== Committing hive changes ===")
try:
    h.commit(None)  # None = write back to same file
    print("Commit SUCCESS")
except Exception as e:
    print(f"Commit FAILED: {e}")

print("\n=== Summary ===")
for path, status in results:
    print(f"  {path}: {status}")
'''

        # Write the script to the rescue system
        write_cmd = "cat > /tmp/fix_hive.py << 'PYEOF'\n" + py_script + "\nPYEOF"
        run_cmd(ssh, write_cmd)

        print("\n=== Running hivex Python script ===")
        run_cmd(ssh, "python3 /tmp/fix_hive.py 2>&1", timeout=120)

    elif 'hivexregedit' in out_hre:
        print("\n=== Using hivexregedit ===")
        # hivexregedit can update existing keys
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
        run_cmd(ssh, f"echo '{reg_content}' > /tmp/mps_fix.reg")
        run_cmd(ssh, f"hivexregedit --merge /mnt/win/Windows/System32/config/SYSTEM --prefix='HKEY_LOCAL_MACHINE\\SYSTEM' /tmp/mps_fix.reg 2>&1", timeout=120)

    else:
        print("\n=== Trying reged -e (interactive edit via expect) ===")
        # Use reged's edit mode with expect to set individual values
        expect_script = r'''#!/usr/bin/expect -f
set timeout 30
spawn reged -e /mnt/win/Windows/System32/config/SYSTEM

# Navigate to MpsSvc and set Start
expect ">" { send "cd \\ControlSet001\\Services\\MpsSvc\r" }
expect ">" { send "setval Start DWORD 4\r" }
expect ">" { send "cd \\\r" }
expect ">" { send "cd \\ControlSet001\\Services\\BFE\r" }
expect ">" { send "setval Start DWORD 4\r" }
expect ">" { send "cd \\\r" }
expect ">" { send "cd \\ControlSet002\\Services\\MpsSvc\r" }
expect ">" { send "setval Start DWORD 4\r" }
expect ">" { send "cd \\\r" }
expect ">" { send "cd \\ControlSet002\\Services\\BFE\r" }
expect ">" { send "setval Start DWORD 4\r" }
expect ">" { send "q\r" }
expect ">" { send "y\r" }
expect eof
'''
        run_cmd(ssh, "cat > /tmp/fix_reged.exp << 'EXPEOF'\n" + expect_script + "\nEXPEOF")
        run_cmd(ssh, "expect /tmp/fix_reged.exp 2>&1", timeout=60)

    # Verify with hivex if available
    print("\n=== Verification: read back Start values ===")
    verify_script = r'''
import struct, hivex
HIVE = "/mnt/win/Windows/System32/config/SYSTEM"
TARGETS = [
    "ControlSet001\\Services\\MpsSvc",
    "ControlSet001\\Services\\BFE",
    "ControlSet002\\Services\\MpsSvc",
    "ControlSet002\\Services\\BFE",
]
h = hivex.Hivex(HIVE)
root = h.root()
def nav(h, root, path):
    node = root
    for part in path.strip("\\").split("\\"):
        children = {h.node_name(c): c for c in h.node_children(node)}
        if part not in children:
            return None
        node = children[part]
    return node

for path in TARGETS:
    node = nav(h, root, path)
    if node is None:
        print(f"{path}: KEY NOT FOUND")
        continue
    vals = {h.value_key(v): v for v in h.node_values(node)}
    if "Start" in vals:
        vtype, vdata = h.value_value(vals["Start"])
        val = struct.unpack("<I", vdata)[0]
        status = "DISABLED (4)" if val == 4 else f"NOT DISABLED ({val})"
        print(f"{path}: Start={val} -> {status}")
    else:
        print(f"{path}: Start value NOT FOUND")
'''
    run_cmd(ssh, f"python3 -c \"{verify_script.replace(chr(34), chr(39))}\" 2>&1")

    # Fallback: use python3 inline to verify
    run_cmd(ssh, r"""python3 << 'PYEOF'
import struct, hivex
HIVE = "/mnt/win/Windows/System32/config/SYSTEM"
TARGETS = [
    "ControlSet001\Services\MpsSvc",
    "ControlSet001\Services\BFE",
    "ControlSet002\Services\MpsSvc",
    "ControlSet002\Services\BFE",
]
h = hivex.Hivex(HIVE)
root = h.root()
def nav(h, root, path):
    node = root
    for part in path.replace("/","\\").strip("\\").split("\\"):
        children = {h.node_name(c): c for c in h.node_children(node)}
        if part not in children:
            return None
        node = children[part]
    return node
for path in TARGETS:
    node = nav(h, root, path)
    if node is None:
        print(f"{path}: KEY NOT FOUND")
        continue
    vals = {h.value_key(v): v for v in h.node_values(node)}
    if "Start" in vals:
        vtype, vdata = h.value_value(vals["Start"])
        val = struct.unpack("<I", vdata)[0]
        status = "DISABLED (4)" if val == 4 else f"NOT DISABLED ({val})"
        print(f"{path}: Start={val} -> {status}")
    else:
        print(f"{path}: Start value NOT FOUND")
PYEOF
""", timeout=30)

    print("\n=== Done. Mount left intact. ===")
    ssh.close()

if __name__ == '__main__':
    main()
