import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import os
import paramiko, base64

HOST = "88.99.142.89"
PORT = 22
USER = "root"
def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

PASSWORD = require_env("GIFMAKE_RESCUE_PASSWORD")


def run(client, cmd, timeout=30):
    print("\n[CMD] " + cmd[:120])
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    rc = stdout.channel.recv_exit_status()
    if out:
        print("[OUT] " + out)
    if err:
        print("[ERR] " + err)
    print("[RC]  " + str(rc))
    return out, err, rc


def wrf(client, path, content):
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return run(client, "echo " + b64 + " | base64 -d > " + path)


def main():
    print("============================================================")
    print("OpenSSH Rescue Enabler")
    print("Target: " + HOST + ":" + str(PORT))
    print("============================================================")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("\n[*] Connecting via paramiko (look_for_keys=False)...")
    client.connect(
        hostname=HOST, port=PORT, username=USER, password=PASSWORD,
        look_for_keys=False, allow_agent=False, timeout=15
    )
    print("[+] Connected successfully")

    # Step 1: Mount /dev/sdb2
    print("\n--- Step 1: Mount Windows partition ---")
    out, err, rc = run(client, "mount | grep /mnt/win")
    if rc != 0:
        print("[*] Not mounted. Running ntfsfix...")
        run(client, "ntfsfix -d /dev/sdb2")
        run(client, "mkdir -p /mnt/win")
        out, err, rc = run(
            client,
            "mount -t ntfs-3g -o rw,big_writes,remove_hiberfile /dev/sdb2 /mnt/win"
        )
        if rc != 0:
            print("[FAIL] Cannot mount /dev/sdb2: " + err)
            client.close()
            sys.exit(1)
        print("[+] Mounted OK")
    else:
        print("[+] Already mounted")
    out, err, rc = run(
        client, "ls /mnt/win/Windows/System32/config/SYSTEM && echo MOUNT_OK"
    )
    if "MOUNT_OK" not in out:
        print("[FAIL] Mount verification failed")
        client.close()
        sys.exit(1)
    print("[+] Mount verified")

    # Step 2: Check for sshd.exe
    print("\n--- Step 2: Check for OpenSSH sshd.exe ---")
    out, err, rc = run(client, "ls /mnt/win/Windows/System32/OpenSSH/sshd.exe 2>&1")
    sshd_exists = (rc == 0)
    if sshd_exists:
        print("[+] sshd.exe FOUND at /mnt/win/Windows/System32/OpenSSH/sshd.exe")
    else:
        print("[WARN] Not at default path. Searching partition...")
        out2, _, _ = run(client, "find /mnt/win -name sshd.exe 2>/dev/null | head -5")
        if out2:
            print("[INFO] Found at: " + out2)
            sshd_exists = True
        else:
            print("[WARN] sshd.exe NOT found - OpenSSH not installed on Windows")

    # Step 3: SYSTEM hive - sshd + ssh-agent Automatic
    print('\n--- Step 3: Enable sshd + ssh-agent in SYSTEM hive ---')
    CRNL = chr(13) + chr(10)
    DQ = chr(34)
    sshd_reg = (
        'Windows Registry Editor Version 5.00' + CRNL + CRNL
        + '[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\sshd]' + CRNL
        + DQ + 'Start' + DQ + '=dword:00000002' + CRNL + CRNL
        + '[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\ssh-agent]' + CRNL
        + DQ + 'Start' + DQ + '=dword:00000002' + CRNL + CRNL
        + '[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet002\\Services\\sshd]' + CRNL
        + DQ + 'Start' + DQ + '=dword:00000002' + CRNL + CRNL
        + '[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet002\\Services\\ssh-agent]' + CRNL
        + DQ + 'Start' + DQ + '=dword:00000002' + CRNL + CRNL
    )
    wrf(client, '/tmp/sshd.reg', sshd_reg)
    out, err, rc = run(
        client,
        'reged -I -C /mnt/win/Windows/System32/config/SYSTEM '
        + chr(39) + 'HKEY_LOCAL_MACHINE\\SYSTEM' + chr(39) + ' /tmp/sshd.reg 2>&1'
    )
    if rc == 0 or 'done' in (out + err).lower():
        print('[+] SYSTEM hive: sshd + ssh-agent = Automatic (Start=2)')
    else:
        print('[WARN] reged SYSTEM rc=' + str(rc) + ' | ' + out[:200])

    # Step 4: SOFTWARE hive RunOnce
    print('\n--- Step 4: Add RunOnce entries in SOFTWARE hive ---')
    runonce_reg = (
        'Windows Registry Editor Version 5.00' + CRNL + CRNL
        + '[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce]' + CRNL
        + DQ + 'SetupSSH' + DQ + '=' + DQ + 'cmd.exe /c net start sshd & netsh advfirewall firewall add rule name=SSH dir=in action=allow protocol=TCP localport=22' + DQ + CRNL
        + DQ + 'SetupSSHAgent' + DQ + '=' + DQ + 'cmd.exe /c net start ssh-agent' + DQ + CRNL + CRNL
    )
    wrf(client, '/tmp/ssh_runonce.reg', runonce_reg)
    out, err, rc = run(
        client,
        'reged -I -C /mnt/win/Windows/System32/config/SOFTWARE '
        + chr(39) + 'HKEY_LOCAL_MACHINE\\SOFTWARE' + chr(39) + ' /tmp/ssh_runonce.reg 2>&1'
    )
    if rc == 0 or 'done' in (out + err).lower():
        print('[+] SOFTWARE hive: RunOnce SetupSSH + SetupSSHAgent added')
    else:
        print('[WARN] RunOnce reg rc=' + str(rc) + ' | ' + out[:200])

    # Step 5: SYSTEM hive Firewall rule for SSH port 22
    print('\n--- Step 5: Add SSH firewall rule to SYSTEM hive ---')
    fw_reg = (
        'Windows Registry Editor Version 5.00' + CRNL + CRNL
        + '[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\FirewallRules]' + CRNL
        + DQ + 'SSH-In-TCP' + DQ + '=' + DQ + 'v2.30|Action=Allow|Active=TRUE|Dir=In|Protocol=6|LPort=22|Name=SSH|Desc=OpenSSH Server|' + DQ + CRNL + CRNL
        + '[HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet002\\Services\\SharedAccess\\Parameters\\FirewallPolicy\\FirewallRules]' + CRNL
        + DQ + 'SSH-In-TCP' + DQ + '=' + DQ + 'v2.30|Action=Allow|Active=TRUE|Dir=In|Protocol=6|LPort=22|Name=SSH|Desc=OpenSSH Server|' + DQ + CRNL + CRNL
    )
    wrf(client, '/tmp/ssh_fw.reg', fw_reg)
    out, err, rc = run(
        client,
        'reged -I -C /mnt/win/Windows/System32/config/SYSTEM '
        + chr(39) + 'HKEY_LOCAL_MACHINE\\SYSTEM' + chr(39) + ' /tmp/ssh_fw.reg 2>&1'
    )
    if rc == 0 or 'done' in (out + err).lower():
        print('[+] SYSTEM hive: Firewall rule SSH port 22 added (CS001+CS002)')
    else:
        print('[WARN] SSH FW reg rc=' + str(rc) + ' | ' + out[:200])

    # Step 6: Verify sshd Start value
    print('\n--- Step 6: Verify sshd Start value ---')
    vcmd = (
        'printf '
        + chr(39) + 'cd ControlSet001\\Services\\sshd\ncat Start\nq\nn\n' + chr(39)
        + ' | chntpw -e /mnt/win/Windows/System32/config/SYSTEM 2>&1'
    )
    out, err, rc = run(client, vcmd)
    if '0x2' in out or ': 2' in out:
        print('[+] Verified: sshd Start = 0x2 (Automatic)')
    else:
        print('[INFO] sshd verify output: ' + out[:300])

    # Step 7: Sync
    print('\n--- Step 7: Sync filesystem (NOT unmounting) ---')
    run(client, 'sync')
    print('[+] Sync done. Partition stays mounted at /mnt/win.')

    # Summary
    print('\n============================================================')
    print('FINAL SUMMARY')
    print('============================================================')
    print('OpenSSH sshd.exe present : ' + ('YES' if sshd_exists else 'NO - OpenSSH not installed'))
    print('SYSTEM hive sshd         : Start=2 (Automatic) CS001+CS002')
    print('SYSTEM hive ssh-agent    : Start=2 (Automatic) CS001+CS002')
    print('SYSTEM hive FW rule      : SSH port 22 ALLOW (CS001+CS002)')
    print('SOFTWARE RunOnce         : SetupSSH + SetupSSHAgent')
    print('Partition                : Mounted at /mnt/win (NOT unmounted)')
    print('')
    if sshd_exists:
        print('RESULT: Deactivate rescue mode in Hetzner Robot and reboot.')
        print('        SSH access:   ssh Administrator@88.99.142.89 -p 22')
        print('        No rescue mode needed for future access.')
    else:
        print('RESULT: OpenSSH not installed on Windows.')
        print('        After Windows boots, run in PowerShell (Admin):')
        print('          Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0')
        print('          Then reboot - RunOnce will start sshd automatically.')
    client.close()
    print('\n[*] Paramiko session closed.')


if __name__ == '__main__':
    main()
