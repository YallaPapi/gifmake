import urllib.request, json, sys, time
sys.stdout.reconfigure(encoding='utf-8')

names = [
    'honeytonedhaze', 'delicatepulse', 'tendercurveink', 'sweetheatmurmur',
    'shadowlaylasessions', 'closelipshade', 'smokyoliviamood', 'ardentemptress',
    'crimsonalouette', 'rosalierouge',
]

for n in names:
    url = f"https://www.reddit.com/user/{n}/about.json"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'check/1.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read().decode()
        print(f"{n}: HTTP {resp.status} — {body[:200]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200] if hasattr(e, 'read') else ''
        print(f"{n}: HTTP {e.code} — {body}")
    time.sleep(1.1)
