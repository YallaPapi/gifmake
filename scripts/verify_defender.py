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

def run(ssh, cmd, timeout=60):
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    rc = stdout.channel.recv_exit_status()
    if out:
        print(f"STDOUT:\n{out}")
    if err:
        print(f"STDERR:\n{err}")
    print(f"RC: {rc}")
    return rc, out, err

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False)
    print("Connected.")

    HIVE = f"{MOUNT_POINT}/Windows/System32/config/SOFTWARE"

    # Check reged help to understand correct -x syntax on this system
    print("\n=== reged --help ===")
    run(ssh, "reged --help 2>&1 | head -30")
    run(ssh, "reged -x 2>&1 | head -30")

    # Try correct reged -x syntax:
    # reged -x <hivefile> <prefixstring> <key> <output.reg>
    # The prefix is HKEY_LOCAL_MACHINE\SOFTWARE, key is relative subkey
    # Some versions want the key as a subpath relative to the prefix

    print("\n=== Try: export all SOFTWARE\\Policies to verify ===")
    rc, out, err = run(ssh, (
        f"reged -x '{HIVE}' 'HKEY_LOCAL_MACHINE\\SOFTWARE' "
        f"'\\Policies\\Microsoft\\Windows Defender' "
        f"/tmp/v_def.reg 2>&1 && cat /tmp/v_def.reg"
    ), timeout=60)

    print("\n=== Try with double-backslash key prefix ===")
    rc, out, err = run(ssh, (
        f"reged -x '{HIVE}' 'HKEY_LOCAL_MACHINE\\\\SOFTWARE' "
        f"'\\\\Policies\\\\Microsoft\\\\Windows Defender' "
        f"/tmp/v_def2.reg 2>&1 && cat /tmp/v_def2.reg"
    ), timeout=60)

    # Alternative: use chntpw / hivexget / hivexsh if available
    print("\n=== Check available tools ===")
    run(ssh, "which hivexget hivexsh hivexregedit chntpw 2>&1")
    run(ssh, "dpkg -l | grep -E 'hive|chntpw|libhive' 2>/dev/null | head -20")

    # Try hivexget if available
    print("\n=== Try hivexget ===")
    run(ssh, (
        f"hivexget '{HIVE}' "
        f"'\\Software\\Policies\\Microsoft\\Windows Defender' 2>&1 | head -20"
    ), timeout=30)

    # Try python-hivex
    print("\n=== Try python3 hivex ===")
    run(ssh, "python3 -c \"import hivex; print('hivex available')\" 2>&1")

    # Use python3 with hivex to read the values directly
    print("\n=== Read values with python3 hivex ===")
    script = f"""
import sys
try:
    import hivex
    h = hivex.Hivex('{HIVE}', write=False)

    def get_key_values(h, path_parts):
        node = h.root()
        for part in path_parts:
            found = False
            for child in h.node_children(node):
                if h.node_name(child).lower() == part.lower():
                    node = child
                    found = True
                    break
            if not found:
                return None, f"Key not found: {{part}}"
        vals = {{}}
        for v in h.node_values(node):
            name = h.value_key(v)
            t, data = h.value_value(v)
            if t == 4:  # REG_DWORD
                import struct
                vals[name] = hex(struct.unpack('<I', data)[0])
            else:
                vals[name] = repr(data)
        return vals, None

    checks = [
        ("Defender Policy", ["Policies","Microsoft","Windows Defender"]),
        ("Defender RTP",    ["Policies","Microsoft","Windows Defender","Real-Time Protection"]),
        ("Defender Features", ["Microsoft","Windows Defender","Features"]),
        ("Explorer Policies", ["Microsoft","Windows","CurrentVersion","Policies","Explorer"]),
        ("WER Policy",       ["Policies","Microsoft","Windows","Windows Error Reporting"]),
    ]
    for label, path in checks:
        vals, err = get_key_values(h, path)
        print(f"\\n[{{label}}]")
        if err:
            print(f"  ERROR: {{err}}")
        else:
            for k, v in vals.items():
                print(f"  {{k}} = {{v}}")
except Exception as e:
    print(f"hivex error: {{e}}")
    sys.exit(1)
"""
    run(ssh, f"python3 -c {repr(script)} 2>&1", timeout=30)

    ssh.close()
    print("\nDone.")

if __name__ == "__main__":
    main()
