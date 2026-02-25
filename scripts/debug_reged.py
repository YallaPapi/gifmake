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
PARTITION = "/dev/sdb2"
MOUNTPOINT = "/mnt/win"
SYSTEM_HIVE = f"{MOUNTPOINT}/Windows/System32/config/SYSTEM"
SOFTWARE_HIVE = f"{MOUNTPOINT}/Windows/System32/config/SOFTWARE"

def run(client, cmd, timeout=30):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    rc = stdout.channel.recv_exit_status()
    return rc, out, err

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD, look_for_keys=False, allow_agent=False)
print("Connected.")

# Mount if needed
rc, out, err = run(client, f"mountpoint -q {MOUNTPOINT} && echo MOUNTED || echo NOT_MOUNTED")
if "NOT_MOUNTED" in out:
    run(client, f"mkdir -p {MOUNTPOINT}")
    rc, out, err = run(client, f"mount -t ntfs-3g -o remove_hiberfile {PARTITION} {MOUNTPOINT}")
    if rc != 0:
        rc, out, err = run(client, f"mount -t ntfs-3g {PARTITION} {MOUNTPOINT}")
    print(f"Mount rc={rc} err={err}")
else:
    print("Already mounted.")

# Test 1: Check reged is available and its help
print("\n--- reged --help ---")
rc, out, err = run(client, "reged --help 2>&1 || reged -? 2>&1 || which reged")
print(f"rc={rc}")
print(out[:500])
print(err[:500])

# Test 2: Try a simple reged export of SYSTEM hive root
print("\n--- reged export of ControlSet001\\Services\\TermService ---")
cmd = f'reged -x "{SYSTEM_HIVE}" "" "ControlSet001\\Services\\TermService" /tmp/ts_export.reg 2>&1; echo "EXIT:$?"'
rc, out, err = run(client, cmd, timeout=20)
print(f"stdout: {out[:1000]}")
print(f"stderr: {err[:500]}")

# Read the exported file
rc2, out2, err2 = run(client, "cat /tmp/ts_export.reg 2>&1 | head -50")
print(f"\n--- /tmp/ts_export.reg (first 50 lines) ---")
print(out2)

# Test 3: Try hivexget as alternative
print("\n--- hivexget availability ---")
rc, out, err = run(client, "which hivexget hivexregedit hivexsh 2>&1; dpkg -l 'hivex*' 2>&1 | grep '^ii'")
print(out)
print(err)

# Test 4: Try chntpw/reged with different syntax
print("\n--- Try reged list root keys ---")
rc, out, err = run(client, f'echo "cd \\\\ControlSet001\\\\Services\\\\TermService\ncat" | reged -e "{SYSTEM_HIVE}" 2>&1 | head -30', timeout=20)
print(out[:500])
print(err[:500])

# Test 5: Check what tools are available for registry
print("\n--- Available registry tools ---")
rc, out, err = run(client, "ls /usr/bin/reged /usr/bin/hivex* /usr/bin/chntpw 2>/dev/null; dpkg -l | grep -i 'hive\\|chntpw\\|ntfs' | grep '^ii'")
print(out)

# Test 6: Try hivexget directly
print("\n--- hivexget TermService\\Start ---")
rc, out, err = run(client, f'hivexget "{SYSTEM_HIVE}" "\\ControlSet001\\Services\\TermService" "Start" 2>&1')
print(f"rc={rc} out={out!r} err={err!r}")

# Test 7: Try with hivexregedit
print("\n--- hivexregedit export ---")
rc, out, err = run(client, f'hivexregedit --export "{SYSTEM_HIVE}" "\\ControlSet001\\Services\\TermService" 2>&1 | head -20')
print(f"rc={rc}\n{out}\n{err}")

client.close()
