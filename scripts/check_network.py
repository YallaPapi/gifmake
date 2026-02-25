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
WIN_PARTITION = "/dev/sdb2"
MOUNT_POINT = "/mnt/win"

EXPECTED = {
    "ip": "88.99.142.89",
    "gateway": "88.99.142.65",
    "dns": ["185.12.64.1", "185.12.64.2"],
    "subnet": "255.255.255.192",  # /26
}

def run(client, cmd, timeout=60):
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out:
        print(out)
    if err:
        print(f"[stderr] {err}")
    return out, err

def main():
    print(f"Connecting to {HOST} as {USER}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username=USER,
        password=PASSWORD,
        look_for_keys=False,
        allow_agent=False,
        timeout=20
    )
    print("Connected.")

    # 1. Check if already mounted
    out, _ = run(client, f"mountpoint -q {MOUNT_POINT} && echo MOUNTED || echo NOT_MOUNTED")
    already_mounted = "MOUNTED" in out

    if not already_mounted:
        print(f"\nMounting {WIN_PARTITION} -> {MOUNT_POINT}...")
        run(client, f"mkdir -p {MOUNT_POINT}")
        out, err = run(client, f"mount -t ntfs-3g -o ro {WIN_PARTITION} {MOUNT_POINT}")
        out2, _ = run(client, f"mountpoint -q {MOUNT_POINT} && echo MOUNTED || echo FAILED")
        if "FAILED" in out2:
            print("ro mount failed, retrying without ro...")
            run(client, f"umount {MOUNT_POINT} 2>/dev/null || true")
            run(client, f"mount -t ntfs-3g {WIN_PARTITION} {MOUNT_POINT}")
    else:
        print(f"{MOUNT_POINT} is already mounted.")

    # 2. Verify SYSTEM hive exists
    run(client, f"ls -la {MOUNT_POINT}/Windows/System32/config/SYSTEM")

    # 3. Write the hivex-based Python script to the rescue system and run it there
    #    (avoids reged encoding issues; hivex is standard in rescue environments)
    remote_script = r"""
import sys, struct, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HIVE = '/mnt/win/Windows/System32/config/SYSTEM'
EXPECTED_IP = '88.99.142.89'
EXPECTED_GW = '88.99.142.65'
EXPECTED_DNS = ['185.12.64.1', '185.12.64.2']
EXPECTED_MASK = '255.255.255.192'

try:
    import hivex
    HAS_HIVEX = True
except ImportError:
    HAS_HIVEX = False

if HAS_HIVEX:
    h = hivex.Hivex(HIVE)

    def find_key(parent, *parts):
        node = parent
        for p in parts:
            node = h.node_get_child(node, p)
            if node is None:
                return None
        return node

    def get_val(node, name):
        try:
            v = h.node_get_value(node, name)
            data = h.value_value(v)
            vtype, raw = data[0], data[1]
            if vtype == 4:   # DWORD
                return str(struct.unpack('<I', raw)[0])
            elif vtype in (1, 2):  # REG_SZ, REG_EXPAND_SZ
                return raw.decode('utf-16-le').rstrip('\x00')
            elif vtype == 7:  # REG_MULTI_SZ
                return raw.decode('utf-16-le').rstrip('\x00').replace('\x00', ' | ')
            else:
                return f'(type={vtype}, {len(raw)} bytes)'
        except Exception:
            return '(not set)'

    root = h.root()

    # Try ControlSet001 first, fall back to CurrentControlSet
    ifaces = find_key(root, 'ControlSet001', 'Services', 'Tcpip', 'Parameters', 'Interfaces')
    if ifaces is None:
        print('ControlSet001 not found, trying CurrentControlSet...')
        ifaces = find_key(root, 'CurrentControlSet', 'Services', 'Tcpip', 'Parameters', 'Interfaces')

    if ifaces is None:
        print('ERROR: Tcpip\\Parameters\\Interfaces not found in any ControlSet')
        sys.exit(1)

    print(f'\nFound Tcpip\\Parameters\\Interfaces key.\n')
    issues = []

    for child in h.node_children(ifaces):
        name = h.node_name(child)
        dhcp       = get_val(child, 'EnableDHCP')
        ip         = get_val(child, 'IPAddress')
        mask       = get_val(child, 'SubnetMask')
        gw         = get_val(child, 'DefaultGateway')
        dns        = get_val(child, 'NameServer')
        dhcp_ip    = get_val(child, 'DhcpIPAddress')
        dhcp_mask  = get_val(child, 'DhcpSubnetMask')
        dhcp_gw    = get_val(child, 'DhcpDefaultGateway')
        dhcp_dns   = get_val(child, 'DhcpNameServer')

        print(f'=== Interface: {name} ===')
        print(f'  EnableDHCP:        {dhcp}')
        print(f'  IPAddress:         {ip}')
        print(f'  SubnetMask:        {mask}')
        print(f'  DefaultGateway:    {gw}')
        print(f'  NameServer:        {dns}')
        print(f'  DhcpIPAddress:     {dhcp_ip}')
        print(f'  DhcpSubnetMask:    {dhcp_mask}')
        print(f'  DhcpDefaultGateway:{dhcp_gw}')
        print(f'  DhcpNameServer:    {dhcp_dns}')

        # Only analyze interfaces that have an IP
        eff_ip   = (ip if ip not in ('(not set)', '0.0.0.0', '') else dhcp_ip)
        eff_mask = (mask if mask not in ('(not set)', '0.0.0.0', '') else dhcp_mask)
        eff_gw   = (gw if gw not in ('(not set)', '0.0.0.0', '') else dhcp_gw)
        eff_dns  = (dns if dns not in ('(not set)', '') else dhcp_dns)

        if eff_ip in ('(not set)', '0.0.0.0', ''):
            print('  [SKIP: no IP configured on this interface]\n')
            continue

        mode = 'DHCP' if dhcp == '1' else 'Static'
        print(f'\n  [ANALYSIS - Mode: {mode}]')
        print(f'  Effective IP:      {eff_ip}')
        print(f'  Effective Mask:    {eff_mask}')
        print(f'  Effective Gateway: {eff_gw}')
        print(f'  Effective DNS:     {eff_dns}')

        def first(s):
            if not s or s == '(not set)':
                return ''
            return s.replace('|', ' ').split()[0].strip()

        ip_ok   = first(eff_ip)   == EXPECTED_IP
        mask_ok = first(eff_mask) == EXPECTED_MASK
        gw_ok   = first(eff_gw)   == EXPECTED_GW

        if not ip_ok:
            issues.append(f'[{name}] IP: got {first(eff_ip)!r}, expected {EXPECTED_IP!r}')
        if not mask_ok:
            issues.append(f'[{name}] Mask: got {first(eff_mask)!r}, expected {EXPECTED_MASK!r}')
        if not gw_ok:
            issues.append(f'[{name}] Gateway: got {first(eff_gw)!r}, expected {EXPECTED_GW!r}')
        for d in EXPECTED_DNS:
            if d not in (eff_dns or ''):
                issues.append(f'[{name}] DNS {d!r} not in {eff_dns!r}')

        print()

    print('=' * 50)
    print('VALIDATION SUMMARY')
    print('=' * 50)
    if issues:
        print('ISSUES FOUND (reporting only, NOT changing):')
        for i in issues:
            print(f'  MISMATCH: {i}')
    else:
        print('All settings match expected configuration:')
        print(f'  IP:      {EXPECTED_IP}  OK')
        print(f'  Gateway: {EXPECTED_GW}  OK')
        print(f'  Mask:    {EXPECTED_MASK}  OK')
        print(f'  DNS:     {", ".join(EXPECTED_DNS)}  OK')

else:
    # Fallback: use reged command-line tool
    print('hivex not available, falling back to reged...')
    import subprocess
    cmd = [
        'reged', '-x',
        '/mnt/win/Windows/System32/config/SYSTEM',
        'HKEY_LOCAL_MACHINE\\SYSTEM',
        '\\ControlSet001\\Services\\Tcpip\\Parameters\\Interfaces',
        '/dev/stdout'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    print(result.stdout)
    if result.stderr:
        print('[stderr]', result.stderr)
"""

    # Write the script to /tmp on the rescue system
    sftp = client.open_sftp()
    with sftp.open('/tmp/check_net.py', 'w') as f:
        f.write(remote_script)
    sftp.close()
    print("\nScript uploaded to /tmp/check_net.py on rescue system.")

    # Run it
    print("\n=== Running network check on rescue system ===\n")
    out, err = run(client, "python3 /tmp/check_net.py", timeout=120)

    print("\nDone. Partition left mounted as instructed.")
    client.close()

if __name__ == "__main__":
    main()
