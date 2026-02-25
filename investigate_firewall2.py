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
    print("Connecting to Hetzner rescue system (Phase 2)...")
    print("=" * 60)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD,
                   look_for_keys=False, allow_agent=False, timeout=30)
    print("Connected.")

    # -------------------------------------------------------
    # KEY FINDING 1: "StartupFix" scheduled task
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 1: StartupFix scheduled task")
    print("=" * 60)
    run(client, "cat '/mnt/win/Windows/System32/Tasks/StartupFix' 2>&1")
    run(client, "cat '/mnt/win/Windows/System32/Tasks/GifMake Warmup' 2>&1")

    # -------------------------------------------------------
    # KEY FINDING 2: FixFirewall in SOFTWARE registry
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 2: Registry SOFTWARE hive - FixFirewall context")
    print("=" * 60)
    # Install hivex if available
    run(client, "apt-get install -y libhivex-bin 2>&1 | tail -5 || echo 'hivex install failed'", timeout=120)
    run(client, "which hivexget hivexsh 2>/dev/null || echo 'hivex not available'")

    # Try python-registry as alternative
    run(client, "pip3 install python-registry 2>&1 | tail -5 || echo 'pip install failed'", timeout=120)
    run(client, "python3 -c 'import Registry; print(\"python-registry available\")' 2>&1")

    # -------------------------------------------------------
    # KEY FINDING 3: strings with wider context around FixFirewall
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 3: SOFTWARE registry - FixFirewall context (strings -n 6)")
    print("=" * 60)
    run(client,
        "strings -n 6 /mnt/win/Windows/System32/config/SOFTWARE "
        "| grep -B5 -A10 -i 'FixFirewall\\|WindowsFirewall' | head -80",
        timeout=60)

    # -------------------------------------------------------
    # KEY FINDING 4: Read SOFTWARE registry with python-registry
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 4: Parse SOFTWARE registry for firewall keys")
    print("=" * 60)

    script = r"""
import sys
try:
    from Registry import Registry
    reg = Registry.Registry("/mnt/win/Windows/System32/config/SOFTWARE")

    # Walk looking for firewall-related keys
    def walk(key, depth=0):
        try:
            name = key.name()
            if any(x in name.lower() for x in ['firewall', 'mpssvc', 'bfe', 'fixfirewall', 'windowsfirewall']):
                print("  " * depth + f"KEY: {key.path()}")
                for val in key.values():
                    try:
                        print("  " * depth + f"  VAL: {val.name()} = {val.value()!r}"[:200])
                    except:
                        print("  " * depth + f"  VAL: {val.name()} = <unreadable>")
            for sub in key.subkeys():
                walk(sub, depth+1)
        except Exception as e:
            pass

    walk(reg.root())
    print("DONE walking SOFTWARE")
except ImportError:
    print("python-registry not available")
except Exception as e:
    print(f"ERROR: {e}")
"""
    run(client, f"python3 -c '{script}' 2>&1", timeout=120)

    # -------------------------------------------------------
    # INVESTIGATION 5: Parse SYSTEM registry for firewall keys
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 5: Parse SYSTEM registry for firewall keys")
    print("=" * 60)

    script2 = r"""
import sys
try:
    from Registry import Registry
    reg = Registry.Registry("/mnt/win/Windows/System32/config/SYSTEM")

    def walk(key, depth=0):
        try:
            name = key.name()
            if any(x in name.lower() for x in ['firewall', 'mpssvc', 'bfe', 'fixfirewall', 'firewallpolicy']):
                print("  " * depth + f"KEY: {key.path()}")
                for val in key.values():
                    try:
                        print("  " * depth + f"  VAL: {val.name()} = {val.value()!r}"[:200])
                    except:
                        print("  " * depth + f"  VAL: {val.name()} = <unreadable>")
            for sub in key.subkeys():
                walk(sub, depth+1)
        except Exception as e:
            pass

    walk(reg.root())
    print("DONE walking SYSTEM")
except ImportError:
    print("python-registry not available")
except Exception as e:
    print(f"ERROR: {e}")
"""
    run(client, f"python3 -c '{script2}' 2>&1", timeout=120)

    # -------------------------------------------------------
    # INVESTIGATION 6: Registry.pol (Group Policy)
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 6: Group Policy Registry.pol content")
    print("=" * 60)
    # Registry.pol is binary but has readable key paths
    run(client,
        "strings -n 5 /mnt/win/Windows/System32/GroupPolicy/Machine/Registry.pol "
        "| grep -i 'firewall\\|MpsSvc\\|EnableFirewall\\|BFE' | head -40",
        timeout=30)
    run(client,
        "strings -n 5 /mnt/win/Windows/System32/GroupPolicy/Machine/Registry.pol "
        "| head -60",
        timeout=30)

    # -------------------------------------------------------
    # INVESTIGATION 7: SoftwareDistribution ReportingEvents.log
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 7: Windows Update ReportingEvents.log")
    print("=" * 60)
    run(client, "cat /mnt/win/Windows/SoftwareDistribution/ReportingEvents.log | tail -50", timeout=30)

    # -------------------------------------------------------
    # INVESTIGATION 8: python-evtx to parse firewall log properly
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 8: Install python-evtx and parse firewall log")
    print("=" * 60)
    run(client, "pip3 install python-evtx 2>&1 | tail -5", timeout=120)

    evtx_script = r"""
import sys
try:
    import Evtx.Evtx as evtx
    import Evtx.Views as e_views
    import lxml.etree as etree

    log_path = '/mnt/win/Windows/System32/winevt/Logs/Microsoft-Windows-Windows Firewall With Advanced Security%4Firewall.evtx'
    print(f"Parsing: {log_path}")
    with evtx.Evtx(log_path) as log:
        for record in log.records():
            try:
                xml = record.xml()
                root = etree.fromstring(xml.encode())
                ns = {'e': 'http://schemas.microsoft.com/win/2004/08/events/event'}
                event_id_el = root.find('.//e:EventID', ns)
                time_el = root.find('.//e:TimeCreated', ns)
                if event_id_el is None:
                    continue
                eid = event_id_el.text
                ts = time_el.get('SystemTime', '') if time_el is not None else ''
                # Print all event data
                data_els = root.findall('.//e:Data', ns)
                data = {d.get('Name', f'Data{i}'): d.text for i, d in enumerate(data_els)}
                print(f"EventID={eid} Time={ts[:19]} Data={data}")
            except Exception as ex:
                pass
    print("DONE parsing firewall log")
except ImportError as e:
    print(f"Import error: {e}")
except Exception as e:
    print(f"ERROR: {e}")
"""
    run(client, f"python3 -c '{evtx_script}' 2>&1", timeout=120)

    # -------------------------------------------------------
    # INVESTIGATION 9: Parse System.evtx for firewall events
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 9: System.evtx - firewall/BFE/MpsSvc events")
    print("=" * 60)

    sys_script = r"""
import sys
try:
    import Evtx.Evtx as evtx
    import lxml.etree as etree

    log_path = '/mnt/win/Windows/System32/winevt/Logs/System.evtx'
    print(f"Parsing: {log_path}")
    firewall_keywords = ['firewall', 'mpssvc', 'bfe', 'windows defender firewall', 'base filtering']
    count = 0
    with evtx.Evtx(log_path) as log:
        for record in log.records():
            try:
                xml = record.xml()
                xml_lower = xml.lower()
                if any(kw in xml_lower for kw in firewall_keywords):
                    root = etree.fromstring(xml.encode())
                    ns = {'e': 'http://schemas.microsoft.com/win/2004/08/events/event'}
                    event_id_el = root.find('.//e:EventID', ns)
                    time_el = root.find('.//e:TimeCreated', ns)
                    provider_el = root.find('.//e:Provider', ns)
                    eid = event_id_el.text if event_id_el is not None else '?'
                    ts = time_el.get('SystemTime', '') if time_el is not None else ''
                    provider = provider_el.get('Name', '') if provider_el is not None else ''
                    data_els = root.findall('.//e:Data', ns)
                    msg_els = root.findall('.//{http://schemas.microsoft.com/win/2004/08/events/event}Message')
                    data = {d.get('Name', f'D{i}'): d.text for i, d in enumerate(data_els)}
                    print(f"EventID={eid} Time={ts[:19]} Provider={provider}")
                    print(f"  Data={str(data)[:200]}")
                    count += 1
                    if count > 100:
                        print("... (truncated at 100 records)")
                        break
            except Exception as ex:
                pass
    print(f"DONE - found {count} relevant records in System.evtx")
except ImportError as e:
    print(f"Import error: {e}")
except Exception as e:
    print(f"ERROR: {e}")
"""
    run(client, f"python3 -c '{sys_script}' 2>&1", timeout=180)

    # -------------------------------------------------------
    # INVESTIGATION 10: Parse GroupPolicy Operational log
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 10: GroupPolicy Operational log (all events)")
    print("=" * 60)

    gp_script = r"""
import sys
try:
    import Evtx.Evtx as evtx
    import lxml.etree as etree

    log_path = '/mnt/win/Windows/System32/winevt/Logs/Microsoft-Windows-GroupPolicy%4Operational.evtx'
    print(f"Parsing: {log_path}")
    count = 0
    with evtx.Evtx(log_path) as log:
        for record in log.records():
            try:
                xml = record.xml()
                root = etree.fromstring(xml.encode())
                ns = {'e': 'http://schemas.microsoft.com/win/2004/08/events/event'}
                event_id_el = root.find('.//e:EventID', ns)
                time_el = root.find('.//e:TimeCreated', ns)
                eid = event_id_el.text if event_id_el is not None else '?'
                ts = time_el.get('SystemTime', '') if time_el is not None else ''
                data_els = root.findall('.//e:Data', ns)
                data = {d.get('Name', f'D{i}'): d.text for i, d in enumerate(data_els)}
                print(f"EventID={eid} Time={ts[:19]} Data={str(data)[:300]}")
                count += 1
                if count > 200:
                    print("... (truncated at 200)")
                    break
            except Exception as ex:
                pass
    print(f"DONE - {count} records in GroupPolicy log")
except Exception as e:
    print(f"ERROR: {e}")
"""
    run(client, f"python3 -c '{gp_script}' 2>&1", timeout=120)

    # -------------------------------------------------------
    # INVESTIGATION 11: Check back/ and back.exe (suspicious!)
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 11: Suspicious back/ and back.exe in Windows dir")
    print("=" * 60)
    run(client, "ls -la /mnt/win/Windows/back* 2>&1", timeout=15)
    run(client, "ls -la /mnt/win/Windows/back/ 2>&1 | head -30", timeout=15)
    run(client, "file /mnt/win/Windows/back.exe 2>&1", timeout=15)
    run(client, "strings /mnt/win/Windows/back.exe 2>/dev/null | grep -i 'firewall\\|netsh\\|cmd\\|powershell\\|enable\\|service' | head -30", timeout=30)
    run(client, "strings /mnt/win/Windows/back.exe 2>/dev/null | head -60", timeout=30)

    # -------------------------------------------------------
    # INVESTIGATION 12: Startup scripts / Run keys
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 12: SOFTWARE Run/RunOnce keys")
    print("=" * 60)

    run_script = r"""
import sys
try:
    from Registry import Registry
    reg = Registry.Registry("/mnt/win/Windows/System32/config/SOFTWARE")

    # Check Run keys
    run_paths = [
        "Microsoft\\Windows\\CurrentVersion\\Run",
        "Microsoft\\Windows\\CurrentVersion\\RunOnce",
        "Microsoft\\Windows\\CurrentVersion\\RunOnceEx",
        "Microsoft\\Windows NT\\CurrentVersion\\Winlogon",
        "Policies\\Microsoft\\Windows\\System",
        "Policies\\Microsoft\\WindowsFirewall",
        "Policies\\Microsoft\\WindowsFirewall\\DomainProfile",
        "Policies\\Microsoft\\WindowsFirewall\\StandardProfile",
    ]
    for path in run_paths:
        try:
            key = reg.open(path)
            print(f"\nKEY: SOFTWARE\\{path}")
            for val in key.values():
                try:
                    print(f"  {val.name()} = {val.value()!r}"[:300])
                except:
                    print(f"  {val.name()} = <unreadable>")
        except Exception:
            pass
    print("DONE Run keys check")
except ImportError:
    print("python-registry not available")
except Exception as e:
    print(f"ERROR: {e}")
"""
    run(client, f"python3 -c '{run_script}' 2>&1", timeout=60)

    # Also check NTUSER.DAT for Run keys
    print("\n" + "=" * 60)
    print("INVESTIGATION 13: NTUSER.DAT - user Run keys")
    print("=" * 60)
    run(client, "find /mnt/win/Users -name 'NTUSER.DAT' 2>/dev/null", timeout=15)

    ntuser_script = r"""
import sys, os
try:
    from Registry import Registry

    ntuser_paths = []
    base = "/mnt/win/Users"
    for user in os.listdir(base):
        p = os.path.join(base, user, "NTUSER.DAT")
        if os.path.exists(p):
            ntuser_paths.append((user, p))

    for user, path in ntuser_paths:
        print(f"\n--- User: {user} ---")
        try:
            reg = Registry.Registry(path)
            for subpath in [
                "Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                "Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce",
                "Software\\Policies\\Microsoft\\WindowsFirewall",
            ]:
                try:
                    key = reg.open(subpath)
                    print(f"  KEY: {subpath}")
                    for val in key.values():
                        try:
                            print(f"    {val.name()} = {val.value()!r}"[:300])
                        except:
                            pass
                except:
                    pass
        except Exception as e:
            print(f"  Error reading {path}: {e}")
    print("DONE NTUSER check")
except ImportError:
    print("python-registry not available")
except Exception as e:
    print(f"ERROR: {e}")
"""
    run(client, f"python3 -c '{ntuser_script}' 2>&1", timeout=60)

    # -------------------------------------------------------
    # INVESTIGATION 14: Full strings on back.exe
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 14: Full content of back/ directory")
    print("=" * 60)
    run(client, "find /mnt/win/Windows/back -type f 2>/dev/null | head -30", timeout=15)
    run(client, "find /mnt/win/Windows/back -type f 2>/dev/null -exec ls -la {} \\; | head -30", timeout=15)

    # -------------------------------------------------------
    # INVESTIGATION 15: Check if there's a firewall reset script
    # -------------------------------------------------------
    print("\n" + "=" * 60)
    print("INVESTIGATION 15: Search for firewall scripts in Windows dirs")
    print("=" * 60)
    run(client,
        "find /mnt/win -name '*.bat' -o -name '*.cmd' -o -name '*.ps1' 2>/dev/null "
        "| grep -v 'WinSxS\\|assembly' | head -30",
        timeout=30)
    run(client,
        "find /mnt/win/Windows/back -type f 2>/dev/null "
        "-exec file {} \\; 2>/dev/null | head -20",
        timeout=15)

    print("\n" + "=" * 60)
    print("Phase 2 investigation complete.")
    print("=" * 60)

    client.close()

if __name__ == "__main__":
    main()
