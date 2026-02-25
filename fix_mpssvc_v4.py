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

def run_cmd(ssh, cmd, timeout=90):
    print(f"\n>>> {cmd[:200]}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print("STDOUT:", out[:4000])
    if err.strip():
        print("STDERR:", err[:2000])
    return out, err

def main():
    print("Connecting...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False, allow_agent=False)
    print("Connected.")

    # Write and run a Python script that:
    # 1. Lists all keys under ControlSet001\Services that contain 'mps' (case-insensitive)
    # 2. Finds MpsSvc and fixes it
    py_script = r'''#!/usr/bin/env python3
import sys, struct, hivex

HIVE = "/mnt/win/Windows/System32/config/SYSTEM"

h = hivex.Hivex(HIVE, write=True)
root = h.root()

def navigate(h, node, path_parts):
    """Navigate using case-insensitive matching."""
    current = node
    for part in path_parts:
        children = {}
        for c in h.node_children(current):
            name = h.node_name(c)
            children[name.lower()] = (name, c)
        key_lower = part.lower()
        if key_lower not in children:
            print(f"  ERROR: '{part}' not found. Available keys (first 30):")
            avail = sorted(children.keys())
            # Show keys containing 'mps' or 'bfe' or 'firewall'
            relevant = [k for k in avail if any(x in k for x in ['mps', 'bfe', 'fire', 'mpf', 'mpsc', 'mpss'])]
            print(f"  Relevant: {relevant}")
            print(f"  First 30: {avail[:30]}")
            return None
        actual_name, current = children[key_lower]
        print(f"  Navigated to '{actual_name}'")
    return current

# List all Services keys that contain 'mp' or 'firewall'
print("=== Searching for MpsSvc-related keys in ControlSet001\\Services ===")
cs1 = navigate(h, root, ["ControlSet001", "Services"])
if cs1:
    children = []
    for c in h.node_children(cs1):
        name = h.node_name(c)
        children.append(name)

    # Find anything with 'mps' or 'firewall' or 'mpf'
    relevant = sorted([n for n in children if any(x in n.lower() for x in ['mps', 'firewall', 'mpf', 'mpsc', 'wfp', 'nsi'])])
    print(f"Relevant service keys: {relevant}")

    # Also search specifically for MpsSvc variants
    all_mp = sorted([n for n in children if n.lower().startswith('mp')])
    print(f"Keys starting with 'mp': {all_mp}")

print("")
print("=== Now fixing with case-insensitive navigation ===")

TARGETS = [
    (["ControlSet001", "Services", "MpsSvc"], "Start", 4),
    (["ControlSet001", "Services", "BFE"],    "Start", 4),
    (["ControlSet002", "Services", "MpsSvc"], "Start", 4),
    (["ControlSet002", "Services", "BFE"],    "Start", 4),
]

results = []
for (path_parts, valname, newval) in TARGETS:
    path_str = "\\".join(path_parts)
    print(f"\nProcessing: {path_str}")
    node = navigate(h, root, path_parts)
    if node is None:
        results.append((path_str, "FAILED - key not found"))
        continue

    # Read current Start value
    cur_val = None
    for v in h.node_values(node):
        if h.value_key(v) == valname:
            vtype, vdata = h.value_value(v)
            cur_val = struct.unpack("<I", vdata)[0]
            print(f"  Current {valname} = {cur_val}")
            break
    if cur_val is None:
        print(f"  {valname} not found in this key")

    # Set value
    try:
        new_data = struct.pack("<I", newval)
        h.node_set_value(node, {"key": valname, "t": 4, "value": new_data})
        print(f"  Set {valname} = {newval} OK")
        results.append((path_str, f"OK: Start={newval}"))
    except Exception as e:
        print(f"  FAILED: {e}")
        results.append((path_str, f"FAILED: {e}"))

print("\nCommitting...")
h.commit(None)
print("Commit SUCCESS")

print("\n=== Summary ===")
for path, status in results:
    print(f"  {path}: {status}")

# Verify
print("\n=== Verification ===")
h2 = hivex.Hivex(HIVE)
root2 = h2.root()
for (path_parts, valname, _) in TARGETS:
    path_str = "\\".join(path_parts)
    node = navigate(h2, root2, path_parts)
    if node is None:
        print(f"  {path_str}: KEY NOT FOUND")
        continue
    for v in h2.node_values(node):
        if h2.value_key(v) == valname:
            vtype, vdata = h2.value_value(v)
            val = struct.unpack("<I", vdata)[0]
            status = "DISABLED (4)" if val == 4 else f"ACTIVE ({val})"
            print(f"  {path_str}: Start={val} -> {status}")
            break
    else:
        print(f"  {path_str}: Start NOT FOUND")
'''

    sftp = ssh.open_sftp()
    with sftp.open('/tmp/fix_hive4.py', 'w') as f:
        f.write(py_script)
    sftp.close()
    print("Script written to /tmp/fix_hive4.py")

    run_cmd(ssh, "python3 /tmp/fix_hive4.py", timeout=120)

    print("\n=== Done. Mount left intact. ===")
    ssh.close()

if __name__ == '__main__':
    main()
