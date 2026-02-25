"""Get full comment text for banned accounts from VPS run JSON."""
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

BANNED = set([
    'afterdarklunagallery','afterdarkmarajournal','barelyarialatehours',
    'barelymiamood','bubblybrittanyb','coysadiesecrets','darkskyeframes',
    'electricellaedit','femmefreyaframe','glossymaragallery','goldengiaglow',
    'hiddenivyfantasy','honeyedskyegallery','honeyhazelhues','justjessiemodeling',
    'kandykaracouture','lovelylexilove','lunarlenalooks','pinkpennypose',
    'scarletsirenshe','shadowmaraconfession','shadowmiaprivateroom',
    'smokyvioletprivatero','softbellaunfiltered','softcoremaradiary',
    'softskyeconfessions','sunnyskyelife','urbanumavibes','vixenvaleriev',
    'wickedbellasessions'
])

REMOTE_SCRIPT = r'''
import json, sys, re
sys.stdout.reconfigure(encoding='utf-8')

data = json.load(open(r'C:\gifmake\data\warmup_reports\run_20260221_082442.json', encoding='utf-8'))
results = data.get('results', [])

def clean(profile):
    name = re.sub(r'^reddit\s*-?\s*', '', profile, flags=re.IGNORECASE).strip()
    name = re.sub(r'^(P|G|F|4u)\s+', '', name).strip().lstrip('- ').strip()
    return name.lower()

output = []
for item in results:
    if not isinstance(item, dict):
        continue
    name = clean(item.get('profile', ''))
    stats = item.get('stats', {})
    if not isinstance(stats, dict):
        continue
    for action in stats.get('action_log', []):
        if not isinstance(action, dict):
            continue
        if action.get('type') == 'comment':
            output.append({
                'a': name,
                's': action.get('sub', ''),
                'u': action.get('url', ''),
                't': action.get('text', ''),
                'st': action.get('style', ''),
            })

print(json.dumps(output, ensure_ascii=False))
'''

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(VPS_HOST, username=VPS_USER, password=VPS_PASS)

sftp = ssh.open_sftp()
with sftp.open('C:/gifmake/_tmp.py', 'w') as f:
    f.write(REMOTE_SCRIPT)
sftp.close()

stdin, stdout, stderr = ssh.exec_command('cd C:\\gifmake && python _tmp.py')
raw = stdout.read().decode('utf-8', errors='replace')
err = stderr.read().decode('utf-8', errors='replace')
if err.strip():
    print(f"STDERR: {err[:300]}")

comments = json.loads(raw)

# Filter to banned accounts
banned_comments = [c for c in comments if c['a'] in BANNED]
banned_no_comments = BANNED - set(c['a'] for c in banned_comments)

print(f"BANNED ACCOUNTS WITH COMMENTS: {len(set(c['a'] for c in banned_comments))}")
print(f"BANNED ACCOUNTS WITH NO COMMENTS: {len(banned_no_comments)}")
if banned_no_comments:
    print(f"  ({', '.join(sorted(banned_no_comments))})")
print()

for c in banned_comments:
    print(f"ACCOUNT: {c['a']}")
    print(f"SUB: r/{c['s']}")
    print(f"STYLE: {c['st']}")
    print(f"POST: {c['u']}")
    print(f"COMMENT: {c['t']}")
    print()

ssh.exec_command('del C:\\gifmake\\_tmp.py')
ssh.close()
