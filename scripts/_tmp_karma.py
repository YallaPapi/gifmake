"""Quick karma check with proper name cleaning."""
import json, sys, re, time, urllib.request
sys.stdout.reconfigure(encoding='utf-8')

names_raw = [
    "reddit -  glossywillowlooks",
    "reddit bot -  ardentemptress",
    "reddit bot -  celinevegafox",
    "reddit bot -  crimsonalouette",
    "reddit bot -  ivorysirenna",
    "reddit bot -  lilithmonroee",
    "reddit bot -  lunaveloure",
    "reddit bot -  ophelialuxee",
    "reddit bot -  ravyndelacroix",
    "reddit bot -  rosalierouge",
    "reddit bot -  sablefleure",
    "reddit bot -  valentinanyx",
    "reddit bot -  vixenmaribel",
    "reddit bot -  wildivykiss",
    "reddit bot -  zaranocturnevane",
    "reddit bot - carmineceleste",
    "reddit bot - mochamused",
    "reddit bot - velvettvenoma",
]

def clean(profile):
    name = re.sub(r'^reddit\s*(bot)?\s*-?\s*', '', profile, flags=re.IGNORECASE).strip()
    name = re.sub(r'^(P|G|F|4u)\s+', '', name).strip().lstrip('- ').strip()
    return name.lower()

print(f"{'Raw':<40} {'Clean':<30} {'Karma':>7} {'Status'}")
print("-" * 95)

alive = 0
banned = 0
for raw in names_raw:
    name = clean(raw)
    url = f"https://www.reddit.com/user/{name}/about.json"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) check/1.0'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        rdata = json.loads(resp.read().decode())
        karma = rdata.get('data', {}).get('total_karma', 0)
        print(f"{raw:<40} {name:<30} {karma:>7} ALIVE")
        alive += 1
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"{raw:<40} {name:<30} {'---':>7} BANNED (404)")
            banned += 1
        elif e.code == 403:
            print(f"{raw:<40} {name:<30} {'---':>7} SUSPENDED (403)")
            banned += 1
        else:
            print(f"{raw:<40} {name:<30} {'---':>7} HTTP {e.code}")
    except Exception as e:
        print(f"{raw:<40} {name:<30} {'---':>7} ERROR: {e}")
    time.sleep(1.1)

print("-" * 95)
print(f"ALIVE: {alive} | BANNED: {banned}")
