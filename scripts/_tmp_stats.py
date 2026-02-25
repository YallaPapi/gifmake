"""Pull stats from latest completed run + live Reddit karma check."""
import json, sys, re, time, urllib.request
sys.stdout.reconfigure(encoding='utf-8')

# Find latest run file
import glob, os
report_dirs = [
    r'C:\Users\Administrator\gifmake\data\warmup_reports',
    r'C:\gifmake\data\warmup_reports',
]

run_files = []
for d in report_dirs:
    run_files.extend(glob.glob(os.path.join(d, 'run_*.json')))

run_files.sort(key=os.path.getmtime, reverse=True)
if not run_files:
    print("NO RUN FILES FOUND")
    sys.exit(1)

latest = run_files[0]
print(f"LATEST_RUN: {latest}")

data = json.load(open(latest, encoding='utf-8'))
print(f"TIMESTAMP: {data.get('timestamp','?')}")
print(f"TOTAL_ACCOUNTS: {data.get('total_accounts','?')}")

results = data.get('results', [])

def clean(profile):
    name = re.sub(r'^reddit\s*-?\s*', '', profile, flags=re.IGNORECASE).strip()
    name = re.sub(r'^(P|G|F|4u)\s+', '', name).strip().lstrip('- ').strip()
    return name.lower()

accounts = []
for item in results:
    if not isinstance(item, dict):
        continue
    name = clean(item.get('profile', ''))
    stats = item.get('stats', {})
    if not isinstance(stats, dict):
        stats = {}

    comments = stats.get('comments', 0)
    upvotes = stats.get('upvotes', 0)
    downvotes = stats.get('downvotes', 0)
    joins = stats.get('joins', 0)
    sessions = stats.get('sessions', 0)
    total_sec = stats.get('total_sec', 0)
    scrolls = stats.get('scrolls', 0)
    posts_clicked = stats.get('posts_clicked', 0)

    accounts.append({
        'name': name,
        'proxy': item.get('proxy_group', ''),
        'status': item.get('status', ''),
        'comments': comments,
        'upvotes': upvotes,
        'downvotes': downvotes,
        'joins': joins,
        'sessions': sessions,
        'total_min': round(total_sec / 60, 1),
        'scrolls': scrolls,
        'posts_clicked': posts_clicked,
    })

print(f"\nRUN STATS ({len(accounts)} accounts):")
print(f"{'Account':<30} {'Proxy':>5} {'Cmts':>5} {'Up':>4} {'Down':>4} {'Join':>4} {'Sess':>4} {'Min':>6} {'Scroll':>6} {'Click':>5} {'Status'}")
print("-" * 120)
for a in sorted(accounts, key=lambda x: x['name']):
    print(f"{a['name']:<30} {a['proxy']:>5} {a['comments']:>5} {a['upvotes']:>4} {a['downvotes']:>4} {a['joins']:>4} {a['sessions']:>4} {a['total_min']:>6.1f} {a['scrolls']:>6} {a['posts_clicked']:>5} {a['status']}")

# Totals
tot_c = sum(a['comments'] for a in accounts)
tot_u = sum(a['upvotes'] for a in accounts)
tot_d = sum(a['downvotes'] for a in accounts)
tot_j = sum(a['joins'] for a in accounts)
tot_m = sum(a['total_min'] for a in accounts)
print("-" * 120)
print(f"{'TOTALS':<30} {'':>5} {tot_c:>5} {tot_u:>4} {tot_d:>4} {tot_j:>4} {'':>4} {tot_m:>6.1f}")

# Live Reddit karma check
print(f"\n\nLIVE REDDIT KARMA CHECK:")
print(f"{'Account':<30} {'Karma':>7} {'Reddit Status'}")
print("-" * 60)

alive_count = 0
banned_count = 0
for a in sorted(accounts, key=lambda x: x['name']):
    name = a['name']
    url = f"https://www.reddit.com/user/{name}/about.json"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) check/1.0'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        rdata = json.loads(resp.read().decode())
        karma = rdata.get('data', {}).get('total_karma', 0)
        print(f"{name:<30} {karma:>7} ALIVE")
        alive_count += 1
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"{name:<30} {'---':>7} BANNED (404)")
            banned_count += 1
        elif e.code == 403:
            print(f"{name:<30} {'---':>7} SUSPENDED (403)")
            banned_count += 1
        else:
            print(f"{name:<30} {'---':>7} HTTP {e.code}")
    except Exception as e:
        print(f"{name:<30} {'---':>7} ERROR: {e}")
    time.sleep(1.1)

print("-" * 60)
print(f"ALIVE: {alive_count} | BANNED: {banned_count} | TOTAL: {len(accounts)}")
