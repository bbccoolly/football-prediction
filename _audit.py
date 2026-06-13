import json, urllib.request, sys
sys.stdout.reconfigure(encoding="utf-8")

# Test all endpoints
tests = [
    ("GET", "/api/status"),
    ("GET", "/api/rankings"),
    ("GET", "/api/calibration"),
]
for method, path in tests:
    try:
        url = "http://127.0.0.1:5000" + path
        r = urllib.request.urlopen(url, timeout=10)
        print("OK " + path + ": " + str(r.status))
    except Exception as e:
        print("FAIL " + path + ": " + str(e))

# Test predict
data = json.dumps({"home_team":"拜仁","away_team":"多特蒙德","league":"德甲","neutral":False,"task_id":"t"}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:5000/predict", data=data, headers={"Content-Type":"application/json"})
r = json.loads(urllib.request.urlopen(req, timeout=30).read())
e = r["ensemble"]
print("OK /predict: " + r.get("home_team","?") + " vs " + r.get("away_team","?") + " | goals=" + str(e["expected_total_goals"]))
print("  ELO: " + str(r["predictions"]["elo"]["elo_home"]) + " vs " + str(r["predictions"]["elo"]["elo_away"]))
print("  HTFT: " + str(r["htft"]["top"][0]))

# Test debug
data2 = json.dumps({"home_team":"拜仁","away_team":"多特蒙德","neutral":False}).encode("utf-8")
req2 = urllib.request.Request("http://127.0.0.1:5000/api/debug_predict", data=data2, headers={"Content-Type":"application/json"})
r2 = json.loads(urllib.request.urlopen(req2, timeout=30).read())
print("OK /api/debug_predict: htft=" + str("htft" in r2) + " | raw_data keys=" + str(len(r2.get("raw_data",{}))))