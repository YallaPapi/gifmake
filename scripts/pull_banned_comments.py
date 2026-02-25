"""Check status of banned accounts in VPS run report with robust name matching."""
import paramiko
import json
import os
import sys
import re

sys.stdout.reconfigure(encoding='utf-8')

def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

VPS_HOST = "88.99.142.89"
VPS_USER = "Administrator"
VPS_PASS = require_env("GIFMAKE_VPS_ADMIN_PASSWORD")

BANNED = [
    'sunnyskyelife','daintyskinafter','hushedvelvetlook','twilightjadeframes',
    'fadedlilypov','maboroshimiyu','quietleafwhisper','silkyrubyglow',
    'hazygeminidream','softskyeshow','lustrouspetrapages','starryeyedecho',
    'sheerjadesnapshots','shadowsandpetals','dreamblushreel','silkenscarlettease',
    'moonshineveil','goldenveraframes','rosylunareels','neonrosiereveal',
    'softscarletscenes','gentlemoonpetal','blazeandbloom','ashenhaze',
    'smokyvioletprivatero','calmglowink','midnightpetalreels','hiddenivyfantasy',
    'shyembersnap'
]

REMOTE_SCRIPT = r'''
import json, sys, re
sys.stdout.reconfigure(encoding='utf-8')

data = json.load(open(r'C:\gifmake\data\warmup_reports\run_20260221_082442.json', encoding='utf-8'))

results = data.get('results', [])
output = []

def clean_profile_name(profile):
    """Extract clean Reddit username from AdsPower profile name."""
    # Remove "reddit" prefix (with any spacing/dash combo)
    name = re.sub(r'^reddit\s*-?\s*', '', profile, flags=re.IGNORECASE).strip()
    # Remove proxy prefix (P, G, F, 4u followed by space)
    name = re.sub(r'^(P|G|F|4u)\s+', '', name).strip()
    # Remove leading dashes and spaces
    name = name.lstrip('- ').strip()
    return name.lower()

for item in results:
    if not isinstance(item, dict):
        continue
    profile = item.get('profile', '')
    name = clean_profile_name(profile)

    status = item.get('status', '')
    stats = item.get('stats', {})
    error = item.get('error', '')

    comment_count = 0
    comments_data = []
    if isinstance(stats, dict):
        comment_count = stats.get('comments', 0)
        action_log = stats.get('action_log', [])
        for action in action_log:
            if isinstance(action, dict) and action.get('type') == 'comment':
                comments_data.append({
                    'sub': action.get('sub', ''),
                    'post_url': action.get('url', ''),
                    'text': action.get('text', ''),
                    'verified': action.get('verified', False),
                    'style': action.get('style', ''),
                })

    output.append({
        'name': name,
        'raw_profile': profile,
        'status': status,
        'comments': comment_count,
        'comments_data': comments_data,
        'error': str(error)[:200] if error else '',
        'proxy_group': item.get('proxy_group', ''),
    })

print(json.dumps(output, ensure_ascii=False))
'''

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASS)

sftp = ssh.open_sftp()
with sftp.open('C:/gifmake/_tmp_extract.py', 'w') as f:
    f.write(REMOTE_SCRIPT)
sftp.close()

stdin, stdout, stderr = ssh.exec_command('cd C:\\gifmake && python _tmp_extract.py')
raw = stdout.read().decode('utf-8', errors='replace')
err = stderr.read().decode('utf-8', errors='replace')

if err.strip():
    print(f"STDERR: {err[:500]}")

all_accounts = json.loads(raw)
banned_set = set(b.lower() for b in BANNED)

print(f"Total accounts in run: {len(all_accounts)}")

# Show all cleaned names for verification
print("\n--- ALL CLEANED NAMES ---")
for a in sorted(all_accounts, key=lambda x: x['name']):
    marker = " << BANNED" if a['name'] in banned_set else ""
    print(f"  '{a['name']}' (from '{a['raw_profile']}') status={a['status']} comments={a['comments']}{marker}")

# Find banned accounts in the run
found_banned = [a for a in all_accounts if a['name'] in banned_set]
not_found = banned_set - set(a['name'] for a in all_accounts)

print(f"\n\nBanned accounts found in run: {len(found_banned)}")
print(f"Banned accounts NOT in run: {len(not_found)}")

if found_banned:
    print("\n" + "=" * 80)
    print("BANNED ACCOUNTS IN THE RUN - DETAILS")
    print("=" * 80)
    for a in found_banned:
        print(f"\nAccount: {a['name']} (profile: {a['raw_profile']})")
        print(f"Status: {a['status']} | Comments: {a['comments']} | Proxy: {a['proxy_group']}")
        if a['error']:
            print(f"Error: {a['error']}")
        for c in a['comments_data']:
            print(f"  r/{c['sub']}: {c['text']}")
            print(f"  Post: {c['post_url']}")
            print(f"  Verified: {c['verified']}")

if not_found:
    print(f"\n--- BANNED ACCOUNTS NOT FOUND IN RUN ({len(not_found)}) ---")
    for n in sorted(not_found):
        print(f"  {n}")

# Print all 29 banned links
print("\n" + "=" * 80)
print("ALL 29 BANNED ACCOUNT LINKS")
print("=" * 80)
for b in sorted(BANNED):
    print(f"https://reddit.com/user/{b}")

ssh.exec_command('del C:\\gifmake\\_tmp_extract.py')
ssh.close()
