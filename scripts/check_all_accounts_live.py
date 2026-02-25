"""Live-check all 52 active warmup accounts against Reddit API."""
import paramiko
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

VPS_HOST = "88.99.142.89"
VPS_USER = "Administrator"
VPS_PASS = require_env("GIFMAKE_VPS_ADMIN_PASSWORD")

# Step 1: Get all account names from VPS run data
REMOTE_SCRIPT = r'''
import json, sys, re
sys.stdout.reconfigure(encoding='utf-8')

data = json.load(open(r'C:\gifmake\data\warmup_reports\run_20260221_082442.json', encoding='utf-8'))
results = data.get('results', [])

def clean_profile_name(profile):
    name = re.sub(r'^reddit\s*-?\s*', '', profile, flags=re.IGNORECASE).strip()
    name = re.sub(r'^(P|G|F|4u)\s+', '', name).strip()
    name = name.lstrip('- ').strip()
    return name

accounts = []
for item in results:
    if not isinstance(item, dict):
        continue
    profile = item.get('profile', '')
    name = clean_profile_name(profile)
    stats = item.get('stats', {})
    comments = stats.get('comments', 0) if isinstance(stats, dict) else 0

    # Get comment details
    comment_data = []
    if isinstance(stats, dict):
        for action in stats.get('action_log', []):
            if isinstance(action, dict) and action.get('type') == 'comment':
                comment_data.append({
                    'sub': action.get('sub', ''),
                    'url': action.get('url', ''),
                    'text': action.get('text', ''),
                    'verified': action.get('verified', False),
                })

    accounts.append({
        'name': name,
        'profile': profile,
        'status': item.get('status', ''),
        'proxy': item.get('proxy_group', ''),
        'comments': comments,
        'comment_data': comment_data,
    })

print(json.dumps(accounts, ensure_ascii=False))
'''

print("Connecting to VPS...")
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
    print(f"VPS STDERR: {err[:300]}")

accounts = json.loads(raw)
print(f"Got {len(accounts)} accounts from VPS run data")

# Step 2: Check each account against Reddit API
print("\nChecking each account against Reddit API...")
print("(This takes ~1 min for 52 accounts at 1 req/sec)")

alive = []
banned = []
errors = []

for i, acct in enumerate(accounts):
    name = acct['name']
    url = f"https://www.reddit.com/user/{name}/about.json"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) check/1.0'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        karma = data.get('data', {}).get('total_karma', 0)
        acct['karma'] = karma
        acct['reddit_status'] = 'alive'
        alive.append(acct)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            acct['karma'] = 0
            acct['reddit_status'] = 'BANNED'
            banned.append(acct)
        elif e.code == 403:
            acct['karma'] = 0
            acct['reddit_status'] = 'SUSPENDED'
            banned.append(acct)
        else:
            acct['karma'] = 0
            acct['reddit_status'] = f'HTTP {e.code}'
            errors.append(acct)
    except Exception as e:
        acct['karma'] = 0
        acct['reddit_status'] = f'ERROR: {e}'
        errors.append(acct)

    if (i + 1) % 10 == 0:
        print(f"  Checked {i+1}/{len(accounts)}...")
    time.sleep(1.1)

# Results
print(f"\n{'='*80}")
print(f"RESULTS: {len(alive)} alive, {len(banned)} banned, {len(errors)} errors")
print(f"{'='*80}")

print(f"\n--- ALIVE ({len(alive)}) ---")
for a in sorted(alive, key=lambda x: -x['karma']):
    print(f"  {a['name']:35s} karma={a['karma']:5d}  comments={a['comments']}  proxy={a['proxy']}")

print(f"\n--- BANNED ({len(banned)}) ---")
for a in sorted(banned, key=lambda x: x['name']):
    print(f"  {a['name']:35s} status={a['reddit_status']}  comments_feb21={a['comments']}  proxy={a['proxy']}")
    for c in a.get('comment_data', []):
        print(f"    r/{c['sub']}: {c['text'][:100]}")
        print(f"    Post: {c['url']}")

if errors:
    print(f"\n--- ERRORS ({len(errors)}) ---")
    for a in errors:
        print(f"  {a['name']:35s} {a['reddit_status']}")

# Print banned account links
if banned:
    print(f"\n{'='*80}")
    print("BANNED ACCOUNT LINKS")
    print(f"{'='*80}")
    for a in sorted(banned, key=lambda x: x['name']):
        print(f"https://reddit.com/user/{a['name']}")

# Cleanup
ssh.exec_command('del C:\\gifmake\\_tmp_extract.py')
ssh.close()
