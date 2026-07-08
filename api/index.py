import os
import math
import json
import time
import requests
from flask import Flask, render_template, request

app = Flask(__name__)

# --- CONFIG ---
ODDS_API_KEY = os.environ.get("d199c60335a985f260877666a8aa5c0f")
FOOTBALL_API_KEY = os.environ.get("faf0ae629f12816a4af994479d0bfd7f")  # api-sports.io
if not ODDS_API_KEY or not FOOTBALL_API_KEY:
    raise RuntimeError("THE_ODDS_API_KEY et FOOTBALL_API_KEY doivent être définies dans l'environnement")

FOOTBALL_API_BASE = "https://v3.football.api-sports.io"
FOOTBALL_HEADERS = {"x-apisports-key": FOOTBALL_API_KEY}

WORLD_CUP_LEAGUE_ID = 1     # FIFA World Cup dans API-Football
SEASON = 2026

# Quota API-Football = 100 req/jour en free tier -> cache disque obligatoire
STATS_CACHE_FILE = "team_stats_cache.json"
STATS_CACHE_TTL = 12 * 3600  # 12h : les stats de forme ne changent pas match par match

TEAM_ID_CACHE_FILE = "team_ids_cache.json"

ODDS_CACHE_TTL = 300
_odds_cache = {}


# --- PERSISTENCE HELPERS ---
def _load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --- API-FOOTBALL: résolution nom -> id ---
def get_team_id(team_name):
    cache = _load_json(TEAM_ID_CACHE_FILE)
    if team_name in cache:
        return cache[team_name]

    resp = requests.get(
        f"{FOOTBALL_API_BASE}/teams",
        headers=FOOTBALL_HEADERS,
        params={"search": team_name},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("response", [])
    if not results:
        return None

    team_id = results[0]["team"]["id"]
    cache[team_name] = team_id
    _save_json(TEAM_ID_CACHE_FILE, cache)
    return team_id


# --- API-FOOTBALL: stats réelles (buts marqués/encaissés en moyenne) ---
def fetch_team_stats(team_name):
    cache = _load_json(STATS_CACHE_FILE)
    now = time.time()

    entry = cache.get(team_name)
    if entry and (now - entry["ts"]) < STATS_CACHE_TTL:
        return entry["data"]

    team_id = get_team_id(team_name)
    if not team_id:
        return None

    resp = requests.get(
        f"{FOOTBALL_API_BASE}/teams/statistics",
        headers=FOOTBALL_HEADERS,
        params={"league": WORLD_CUP_LEAGUE_ID, "season": SEASON, "team": team_id},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json().get("response", {})

    goals_for = data.get("goals", {}).get("for", {}).get("average", {}).get("total")
    goals_against = data.get("goals", {}).get("against", {}).get("average", {}).get("total")

    if goals_for is None or goals_against is None:
        return None  # équipe sans historique dispo (ex: pas encore joué dans ce tournoi)

    stats = {"avg_scored": float(goals_for), "avg_conceded": float(goals_against)}
    cache[team_name] = {"ts": now, "data": stats}
    _save_json(STATS_CACHE_FILE, cache)
    return stats


# --- FALLBACK si équipe sans stats (ex: tout début de tournoi) ---
LEAGUE_AVG_GOALS = 1.35  # moyenne de buts/équipe/match, ballpark Coupe du Monde
DEFAULT_STATS = {"avg_scored": LEAGUE_AVG_GOALS, "avg_conceded": LEAGUE_AVG_GOALS}


def get_stats(team_name):
    stats = fetch_team_stats(team_name)
    return (stats if stats else DEFAULT_STATS), (stats is not None)


# --- MOTEUR POISSON ---
def poisson_prob(k, lamb):
    return (lamb ** k * math.exp(-lamb)) / math.factorial(k)


def get_match_probas(home_team, away_team):
    h, h_real = get_stats(home_team)
    a, a_real = get_stats(away_team)

    # lambda = moyenne des forces attaque/défense des deux équipes, + avantage domicile
    lambda_home = ((h["avg_scored"] + a["avg_conceded"]) / 2) * 1.15
    lambda_away = ((a["avg_scored"] + h["avg_conceded"]) / 2) * 0.90

    home_win = draw = away_win = 0
    for i in range(6):
        for j in range(6):
            prob = poisson_prob(i, lambda_home) * poisson_prob(j, lambda_away)
            if i > j:
                home_win += prob
            elif i == j:
                draw += prob
            else:
                away_win += prob

    return {
        "home": home_win, "draw": draw, "away": away_win,
        "l_h": lambda_home, "l_a": lambda_away,
        "has_real_stats": h_real and a_real,
    }


def advanced_predictive_engine(market_key, selection, point, stats):
    if market_key == "h2h":
        if selection == "Home":
            return round(stats["home"], 3)
        if selection == "Draw":
            return round(stats["draw"], 3)
        return round(stats["away"], 3)

    elif market_key == "totals":
        target = float(point)
        prob_over = sum(
            poisson_prob(i, stats["l_h"]) * poisson_prob(j, stats["l_a"])
            for i in range(6) for j in range(6) if (i + j) > target
        )
        return round(prob_over, 3) if selection == "Over" else round(1 - prob_over, 3)

    elif market_key == "btts":
        prob_home_goal = 1 - poisson_prob(0, stats["l_h"])
        prob_away_goal = 1 - poisson_prob(0, stats["l_a"])
        prob_btts = prob_home_goal * prob_away_goal
        return round(prob_btts, 3) if selection == "Yes" else round(1 - prob_btts, 3)

    return None


# --- COTES (The Odds API) ---
def fetch_live_odds(sport_key):
    now = time.time()
    cached = _odds_cache.get(sport_key)
    if cached and (now - cached["ts"]) < ODDS_CACHE_TTL:
        return cached["data"]

    try:
        url = (
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
            f"?regions=eu&markets=h2h,totals,btts&oddsFormat=decimal&apiKey={ODDS_API_KEY}"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _odds_cache[sport_key] = {"ts": now, "data": data}
        return data
    except requests.RequestException as e:
        print(f"[fetch_live_odds] Erreur: {e}")
        return cached["data"] if cached else []


def best_odds_per_outcome(bookmakers, market_key, outcome_name, point=None):
    best = None
    for bm in bookmakers:
        for m in bm.get("markets", []):
            if m["key"] != market_key:
                continue
            for o in m.get("outcomes", []):
                if o["name"] != outcome_name:
                    continue
                if point is not None and o.get("point") != point:
                    continue
                if best is None or o["price"] > best:
                    best = o["price"]
    return best


@app.route('/')
def home():
    sport_key = request.args.get('league', 'soccer_world_cup')
    raw_data = fetch_live_odds(sport_key)
    processed = []

    for item in raw_data:
        home_team = item.get("home_team")
        away_team = item.get("away_team")
        bookmakers = item.get("bookmakers", [])

        if not bookmakers:
            continue

        match_stats = get_match_probas(home_team, away_team)

        ref_markets = bookmakers[0].get("markets", [])
        marches = []
        for m in ref_markets:
            outcomes = []
            for o in m.get("outcomes", []):
                p_ia = advanced_predictive_engine(m["key"], o["name"], o.get("point"), match_stats)
                if p_ia is None:
                    continue

                best_price = best_odds_per_outcome(bookmakers, m["key"], o["name"], o.get("point"))
                cote = best_price if best_price else o["price"]

                val = p_ia * float(cote)
                outcomes.append({
                    "name": o["name"],
                    "cote": cote,
                    "p_ia": p_ia,
                    "is_value": val > 1.05,
                    "confidence": "haute" if match_stats["has_real_stats"] else "faible (pas d'historique tournoi)",
                })
            marches.append({"titre": m["key"], "options": outcomes})

        processed.append({"affiche": f"{home_team} vs {away_team}", "marches": marches})

    return render_template('index.html', matches=processed)


if __name__ == "__main__":
    app.run(debug=False)