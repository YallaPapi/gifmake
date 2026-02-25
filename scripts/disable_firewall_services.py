import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import os
import paramiko
import time
import io

HOST = "88.99.142.89"
USER = "root"
def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

PASSWORD = require_env("GIFMAKE_RESCUE_PASSWORD")

BASH_SCRIPT = r"""#!/bin/bash
HIVE="/mnt/win/Windows/System32/config/SYSTEM"
SERVICES="MpsSvc BFE SecurityHealthService WinDefend Sense WdNisSvc wuauserv WaaSMedicSvc UsoSvc TrustedInstaller BITS"

echo "=== STEP 1: MOUNT ==="
mkdir -p /mnt/win
if ! mount | grep -q "/dev/sdb2"; then
    mount -t ntfs-3g -o rw,big_writes,remove_hiberfile /dev/sdb2 /mnt/win
    echo "Mounted sdb2 -> /mnt/win"
else
    echo "Already mounted"
fi
mount | grep sdb2
ls -la "$HIVE" || { echo "FATAL: HIVE NOT FOUND"; exit 1; }
echo "HIVE OK"
echo ""

echo "=== STEP 2: DISABLE SERVICES ==="
for svc in $SERVICES; do
  for cs in ControlSet001 ControlSet002; do
    # Check if key exists
    printf "cd %s\\Services\\%s\nls\n" "$cs" "$svc" > /tmp/_chk.txt
    chk_out=$(chntpw -e "$HIVE" < /tmp/_chk.txt 2>&1)
    if echo "$chk_out" | grep -qi "no such\|cannot\|error"; then
      echo "SKIP|$svc|$cs|not_found"
      continue
    fi

    # Write .reg file (CRLF line endings for Windows registry format)
    reg="/tmp/reg_${cs}_${svc}.reg"
    printf "Windows Registry Editor Version 5.00\r\n\r\n[HKEY_LOCAL_MACHINE\\SYSTEM\\%s\\Services\\%s]\r\n\"Start\"=dword:00000004\r\n\r\n" "$cs" "$svc" > "$reg"

    # Apply with reged -C (auto-commit changed hives)
    apply_out=$(reged -C "$HIVE" "HKEY_LOCAL_MACHINE\SYSTEM" "$reg" 2>&1)

    # Verify
    printf "cd %s\\Services\\%s\ncat Start\n" "$cs" "$svc" > /tmp/_ver.txt
    ver_out=$(chntpw -e "$HIVE" < /tmp/_ver.txt 2>&1)

    if echo "$ver_out" | grep -qi "00000004\|value 4\|dword.*4"; then
      echo "DISABLED|$svc|$cs|Start=4 confirmed"
    elif echo "$apply_out" | grep -qi "changed\|written\|saved"; then
      echo "APPLIED|$svc|$cs|hive_written_verify_unclear|$ver_out"
    else
      echo "RESULT|$svc|$cs|$(echo "$apply_out" | grep -v '^$' | tail -2 | tr '\n' ' ')"
    fi
  done
done

echo ""
echo "=== STEP 3: FINAL VERIFICATION ==="
for svc in $SERVICES; do
  printf "cd ControlSet001\\Services\\%s\ncat Start\n" "$svc" > /tmp/_fver.txt
  fver=$(chntpw -e "$HIVE" < /tmp/_fver.txt 2>&1)
  start_hex=$(echo "$fver" | grep -oiE '0x[0-9a-f]+|value [0-9]+' | head -1)
  echo "FINAL|$svc|ControlSet001|$start_hex"
done

echo ""
echo "NOTE: /mnt/win left mounted as requested."
echo "=== COMPLETE ==="
"""

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, username=USER, password=PASSWORD,
               look_for_keys=False, allow_agent=False, timeout=30)
print("Connected.")

# Upload via SFTP
sftp = client.open_sftp()
f = sftp.open("/tmp/disable_svcs.sh", "w")
f.write(BASH_SCRIPT)
f.close()
sftp.close()
print("Script uploaded. Executing...")

# Execute with streaming output
stdin, stdout, stderr = client.exec_command(
    "bash /tmp/disable_svcs.sh", timeout=600, get_pty=False
)

start = time.time()
results = {"disabled": [], "skipped": [], "failed": []}

while True:
    if stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
        break
    if time.time() - start > 550:
        print("[TIMEOUT]")
        break
    if stdout.channel.recv_ready():
        chunk = stdout.channel.recv(8192).decode("utf-8", errors="replace")
        sys.stdout.write(chunk)
        sys.stdout.flush()
        for line in chunk.splitlines():
            if line.startswith("DISABLED|"):
                parts = line.split("|")
                if len(parts) >= 3:
                    results["disabled"].append(f"{parts[1]}/{parts[2]}")
            elif line.startswith("SKIP|"):
                parts = line.split("|")
                if len(parts) >= 3:
                    results["skipped"].append(f"{parts[1]}/{parts[2]}")
            elif line.startswith("RESULT|") or line.startswith("APPLIED|"):
                parts = line.split("|")
                if len(parts) >= 3:
                    results["failed"].append(f"{parts[1]}/{parts[2]}")
    else:
        time.sleep(0.3)

remaining = stdout.read()
if remaining:
    text = remaining.decode("utf-8", errors="replace")
    sys.stdout.write(text)
    for line in text.splitlines():
        if line.startswith("DISABLED|"):
            parts = line.split("|")
            results["disabled"].append(f"{parts[1]}/{parts[2]}")
        elif line.startswith("SKIP|"):
            parts = line.split("|")
            results["skipped"].append(f"{parts[1]}/{parts[2]}")

err_out = stderr.read()
if err_out:
    print("\nSTDERR:", err_out.decode("utf-8", errors="replace")[:500])

rc = stdout.channel.recv_exit_status()

print("\n" + "="*60)
print("SUMMARY")
print("="*60)
print(f"DISABLED ({len(results['disabled'])}):")
for x in results["disabled"]:
    print(f"  [OK] {x}")
print(f"\nSKIPPED ({len(results['skipped'])}):")
for x in results["skipped"]:
    print(f"  [--] {x}")
print(f"\nFAILED/CHECK ({len(results['failed'])}):")
for x in results["failed"]:
    print(f"  [!!] {x}")
print(f"\nExit code: {rc}")
client.close()
print("Done.")
