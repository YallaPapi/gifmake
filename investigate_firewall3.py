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

def run(client, cmd, timeout=60):
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    if out:
        print(out)
    if err:
        print(f"[STDERR] {err}")
    return out, err

def main():
    print("=" * 60)
    print("Connecting - Phase 3: Read critical files")
    print("=" * 60)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD,
                   look_for_keys=False, allow_agent=False, timeout=30)
    print("Connected.")

    # THE SMOKING GUN: startup-fix.bat
    print("\n" + "=" * 60)
    print("KEY FILE 1: C:\\Windows\\startup-fix.bat")
    print("=" * 60)
    run(client, "cat /mnt/win/Windows/startup-fix.bat 2>&1 || echo 'NOT FOUND'")
    run(client, "ls -la /mnt/win/Windows/startup-fix.bat 2>/dev/null || echo 'file not present'")

    # Also check root of C:
    run(client, "ls /mnt/win/*.bat /mnt/win/*.cmd /mnt/win/*.ps1 2>/dev/null")
    run(client, "cat /mnt/win/startup-fix.bat 2>/dev/null || echo 'not at root'")

    # network-setup.ps1
    print("\n" + "=" * 60)
    print("KEY FILE 2: network-setup.ps1")
    print("=" * 60)
    run(client, "cat /mnt/win/network-setup.ps1 2>&1")

    # back.bat
    print("\n" + "=" * 60)
    print("KEY FILE 3: back/back.bat")
    print("=" * 60)
    run(client, "cat /mnt/win/Windows/back/back.bat 2>&1")

    # admin.bat
    print("\n" + "=" * 60)
    print("KEY FILE 4: back/admin.bat")
    print("=" * 60)
    run(client, "cat /mnt/win/Windows/back/admin.bat 2>&1")

    # registery.reg
    print("\n" + "=" * 60)
    print("KEY FILE 5: back/registery.reg")
    print("=" * 60)
    run(client, "cat /mnt/win/Windows/back/registery.reg 2>&1")

    # gifmake run.bat and run_warmup.bat
    print("\n" + "=" * 60)
    print("KEY FILE 6: gifmake run.bat")
    print("=" * 60)
    run(client, "cat /mnt/win/gifmake/run.bat 2>&1")
    run(client, "cat /mnt/win/gifmake/run_warmup.bat 2>&1")

    # Use hivexget to read registry keys now that it's installed
    print("\n" + "=" * 60)
    print("REGISTRY: Run keys via hivexget")
    print("=" * 60)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SOFTWARE "
        "'Microsoft\\Windows\\CurrentVersion\\Run' 2>&1 || echo 'key not found'",
        timeout=30)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SOFTWARE "
        "'Microsoft\\Windows\\CurrentVersion\\RunOnce' 2>&1 || echo 'key not found'",
        timeout=30)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SOFTWARE "
        "'Policies\\Microsoft\\WindowsFirewall' 2>&1 || echo 'key not found'",
        timeout=30)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SOFTWARE "
        "'Policies\\Microsoft\\WindowsFirewall\\StandardProfile' 2>&1 || echo 'key not found'",
        timeout=30)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SOFTWARE "
        "'Policies\\Microsoft\\WindowsFirewall\\DomainProfile' 2>&1 || echo 'key not found'",
        timeout=30)

    # SYSTEM registry - FirewallPolicy
    print("\n" + "=" * 60)
    print("REGISTRY: SYSTEM hive - FirewallPolicy")
    print("=" * 60)
    # Find ControlSet
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SYSTEM "
        "'ControlSet001\\Services\\MpsSvc' 2>&1 | head -20 || echo 'not found'",
        timeout=30)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SYSTEM "
        "'ControlSet001\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\StandardProfile' "
        "2>&1 | head -20 || echo 'not found'",
        timeout=30)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SYSTEM "
        "'ControlSet001\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\DomainProfile' "
        "2>&1 | head -20 || echo 'not found'",
        timeout=30)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SYSTEM "
        "'ControlSet001\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\PublicProfile' "
        "2>&1 | head -20 || echo 'not found'",
        timeout=30)

    # Check hivexsh to dump firewall policy
    print("\n" + "=" * 60)
    print("REGISTRY: hivexsh dump of firewall EnableFirewall values")
    print("=" * 60)
    # Use hivexsh to walk the firewall policy
    script = """open /mnt/win/Windows/System32/config/SYSTEM
cd ControlSet001\\Services\\SharedAccess\\Parameters\\FirewallPolicy
ls
"""
    run(client, f"echo '{script}' | hivexsh 2>&1 || echo 'hivexsh failed'", timeout=30)

    # Check the Winlogon shell
    print("\n" + "=" * 60)
    print("REGISTRY: Winlogon shell & userinit")
    print("=" * 60)
    run(client,
        "hivexget /mnt/win/Windows/System32/config/SOFTWARE "
        "'Microsoft\\Windows NT\\CurrentVersion\\Winlogon' 2>&1 | head -20",
        timeout=30)

    # Check for any scheduled tasks that mention firewall
    print("\n" + "=" * 60)
    print("SCHEDULED TASKS: All task files content summary")
    print("=" * 60)
    run(client,
        "for f in /mnt/win/Windows/System32/Tasks/*; do "
        "echo \"=== $f ===\"; "
        "iconv -f utf-16 -t utf-8 \"$f\" 2>/dev/null | grep -i 'command\\|firewall\\|netsh\\|description' | head -5; "
        "done 2>/dev/null",
        timeout=30)

    # Check Task Scheduler Microsoft subtasks
    print("\n" + "=" * 60)
    print("SCHEDULED TASKS: Microsoft subfolder tasks")
    print("=" * 60)
    run(client, "find /mnt/win/Windows/System32/Tasks/Microsoft -type f 2>/dev/null | head -20", timeout=15)

    # Check for startup scripts in ProgramData
    print("\n" + "=" * 60)
    print("STARTUP SCRIPTS: GroupPolicy startup scripts")
    print("=" * 60)
    run(client, "find /mnt/win/Windows/System32/GroupPolicy -type f 2>/dev/null", timeout=15)
    run(client, "cat /mnt/win/Windows/System32/GroupPolicy/gpt.ini 2>&1", timeout=10)
    run(client, "find /mnt/win/Windows/System32/GroupPolicy/Machine/Scripts -type f 2>/dev/null | head -20", timeout=15)
    run(client, "ls /mnt/win/Windows/System32/GroupPolicy/Machine/ 2>/dev/null", timeout=10)

    # Check scripts.ini
    run(client, "find /mnt/win/Windows/System32/GroupPolicy -name 'scripts.ini' -o -name 'Scripts' 2>/dev/null", timeout=15)
    run(client, "ls /mnt/win/Windows/System32/GroupPolicy/Machine/Scripts/ 2>/dev/null", timeout=10)
    run(client, "ls /mnt/win/Windows/System32/GroupPolicy/Machine/Scripts/Startup/ 2>/dev/null", timeout=10)
    run(client, "cat /mnt/win/Windows/System32/GroupPolicy/Machine/Scripts/psscripts.ini 2>/dev/null || echo 'no psscripts.ini'", timeout=10)
    run(client, "cat /mnt/win/Windows/System32/GroupPolicy/Machine/Scripts/scripts.ini 2>/dev/null || echo 'no scripts.ini'", timeout=10)

    print("\n" + "=" * 60)
    print("Phase 3 complete.")
    print("=" * 60)

    client.close()

if __name__ == "__main__":
    main()
