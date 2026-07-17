
"""data/fifa_collector.py -- FIFA API national team data collector"""
import requests, json, time, os, sys
from datetime import datetime

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# National team competitions to collect
NT_COMPETITIONS = [
    "FIFA World Cup", "World Cup", "FIFA World Cup Qualifier",
    "UEFA European Championship", "UEFA Euro", "European Championship",
    "Copa America", "CONMEBOL Copa America",
    "African Cup of Nations", "AFC Asian Cup",
    "CONCACAF Gold Cup", "Friendlies", "FIFA Friendlies",
    "UEFA Nations League", "CONMEBOL World Cup Qualifiers",
    "UEFA World Cup Qualifiers", "AFC World Cup Qualifiers",
    "CAF World Cup Qualifiers", "CONCACAF World Cup Qualifiers",
]

ENGLISH_TEAM_NAMES = {
    "England": "???", "Germany": "??", "France": "??", "Spain": "???",
    "Italy": "???", "Netherlands": "??", "Portugal": "???", "Belgium": "???",
    "Brazil": "??", "Argentina": "???", "Uruguay": "???", "Colombia": "????",
    "Chile": "??", "Peru": "??", "Ecuador": "????",
    "Mexico": "???", "USA": "??", "Canada": "???", "Costa Rica": "?????",
    "Japan": "??", "South Korea": "??", "Korea Republic": "??",
    "Australia": "????", "Saudi Arabia": "?????", "Iran": "??",
    "Qatar": "???", "Morocco": "???", "Senegal": "????", "Tunisia": "???",
    "Algeria": "?????", "Egypt": "??", "Nigeria": "????",
    "Cameroon": "???", "Ghana": "??", "Ivory Coast": "????",
    "C?te d'Ivoire": "????", "South Africa": "??",
    "Croatia": "????", "Serbia": "????", "Switzerland": "??",
    "Denmark": "??", "Sweden": "??", "Norway": "??", "Poland": "??",
    "Austria": "???", "Czech Republic": "??", "Czechia": "??",
    "Slovakia": "????", "Hungary": "???", "Romania": "????",
    "Turkey": "???", "Greece": "??", "Ukraine": "???",
    "Russia": "???", "Wales": "???", "Scotland": "???",
    "Paraguay": "???", "Bolivia": "????", "Venezuela": "????",
    "Panama": "???", "Honduras": "????", "Jamaica": "???",
    "Iraq": "???", "New Zealand": "???",
    "Bosnia": "??", "Bosnia and Herzegovina": "??",
    "Finland": "??", "Ireland": "???", "Northern Ireland": "????",
    "Iceland": "??", "Bulgaria": "????", "Slovenia": "?????",
}

def translate_team(english_name):
    """Try to translate English team name to Chinese"""
    if english_name in ENGLISH_TEAM_NAMES:
        return ENGLISH_TEAM_NAMES[english_name]
    return english_name

def collect_fifa_data():
    """Collect national team matches from FIFA API"""
    all_matches = []
    
    # Date ranges for national team windows (2024-2026)
    date_ranges = [
        ("2026-06-01", "2026-07-20"),  # World Cup 2026
        ("2026-03-15", "2026-03-31"),  # March international break
        ("2025-11-01", "2025-11-20"),  # November internationals
        ("2025-10-01", "2025-10-20"),  # October internationals
        ("2025-09-01", "2025-09-15"),  # September internationals
        ("2025-06-01", "2025-06-20"),  # June internationals
        ("2025-03-15", "2025-03-31"),  # March internationals
        ("2024-11-01", "2024-11-20"),  # November internationals
        ("2024-10-01", "2024-10-20"),  # October internationals
        ("2024-09-01", "2024-09-15"),  # September internationals
        ("2024-06-01", "2024-07-20"),  # Euro/Copa 2024
        ("2024-03-15", "2024-03-31"),  # March internationals
    ]
    
    for date_from, date_to in date_ranges:
        url = f"https://api.fifa.com/api/v3/calendar/matches?from={date_from}T00:00:00Z&to={date_to}T23:59:59Z&language=en&count=200"
        try:
            print(f"  Fetching {date_from} to {date_to}...", end=" ")
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"HTTP {r.status_code}")
                continue
            
            data = r.json()
            results = data.get("Results", [])
            count = 0
            
            for m in results:
                # Get competition name
                comp = ""
                if m.get("CompetitionName"):
                    for cn in m["CompetitionName"]:
                        if cn.get("Locale","").startswith("en"):
                            comp = cn.get("Description","")
                            break
                
                # Filter national team competitions
                is_nt = any(ntc in comp for ntc in NT_COMPETITIONS)
                if not is_nt:
                    continue
                
                # Only completed matches
                status = m.get("MatchStatus", 0)
                if status != 8:  # 8 = finished
                    continue
                
                # Get team names
                home_name = ""
                away_name = ""
                if m.get("Home") and m["Home"].get("TeamName"):
                    for tn in m["Home"]["TeamName"]:
                        if tn.get("Locale","").startswith("en"):
                            home_name = tn.get("Description","")
                            break
                if m.get("Away") and m["Away"].get("TeamName"):
                    for tn in m["Away"]["TeamName"]:
                        if tn.get("Locale","").startswith("en"):
                            away_name = tn.get("Description","")
                            break
                
                if not home_name or not away_name:
                    continue
                
                home_cn = translate_team(home_name)
                away_cn = translate_team(away_name)
                
                match_date = (m.get("Date","") or "")[:10]
                if not match_date:
                    continue
                
                all_matches.append({
                    "home_team": home_cn,
                    "away_team": away_cn,
                    "home_goals": m.get("HomeTeamScore", 0) or 0,
                    "away_goals": m.get("AwayTeamScore", 0) or 0,
                    "league": "???" if "Friendlies" in comp else "???" if "World Cup" in comp else comp,
                    "date": match_date,
                    "date_time": m.get("Date",""),
                })
                count += 1
            
            print(f"{count} matches (total: {len(all_matches)})")
            time.sleep(0.3)
            
        except Exception as e:
            print(f"Error: {e}")
    
    print(f"\nTotal collected: {len(all_matches)} national team matches")
    return all_matches

if __name__ == "__main__":
    matches = collect_fifa_data()
    print(f"Done. {len(matches)} matches.")
