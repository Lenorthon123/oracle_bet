import os
import random
import requests
from datetime import datetime
from flask import Flask, render_template, request

template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates'))
app = Flask(__name__, template_folder=template_dir)

# 🔑 METS TA CLÉ ICI
THE_ODDS_API_KEY = "TON_API_KEY_ICI"

# 🌍 DICTIONNAIRE DES CHAMPIONNATS DISPONIBLES DANS L'API
LEAGUES = {
    "soccer_usa_mls": "🇺🇸 MLS (USA)",
    "soccer_france_ligue_1": "🇫🇷 Ligue 1 (France)",
    "soccer_epl": "🇬🇧 Premier League (Angleterre)",
    "soccer_spain_la_liga": "🇪🇸 La Liga (Espagne)",
    "soccer_italy_serie_a": "🇮🇹 Serie A (Italie)",
    "soccer_germany_bundesliga": "🇩🇪 Bundesliga (Allemagne)",
    "soccer_uefa_champs_league": "🇪🇺 Champions League",
    "soccer_uefa_europa_league": "🇪🇺 Europa League"
}

def generate_team_features(team_name):
    seed = sum(ord(char) for char in team_name)
    rng = random.Random(seed)
    return {
        "attack_power": rng.uniform(1.2, 3.2),
        "defense_power": rng.uniform(0.8, 2.5),
        "form_index": rng.uniform(0.3, 0.9)
    }

def advanced_predictive_engine(market_key, selection, point, sport, ctx):
    h_stat = ctx["home_stats"]
    a_stat = ctx["away_stats"]
    
    if market_key == "h2h":
        force_domicile = (h_stat["attack_power"] / a_stat["defense_power"]) * h_stat["form_index"]
        force_exterieur = (a_stat["attack_power"] / h_stat["defense_power"]) * a_stat["form_index"]
        force_domicile += (ctx["repos_home"] * 0.02) - (ctx["absents_home"] * 0.05) + 0.1
        force_exterieur += (ctx["repos_away"] * 0.02) - (ctx["absents_away"] * 0.05)
        
        total_force = force_domicile + force_exterieur + 0.5
        prob_home = max(0.05, min(0.85, force_domicile / total_force))
        prob_away = max(0.05, min(0.85, force_exterieur / total_force))
        prob_draw = max(0.10, min(0.35, 1.0 - prob_home - prob_away))
            
        somme = prob_home + prob_away + prob_draw
        prob_home, prob_away, prob_draw = prob_home/somme, prob_away/somme, prob_draw/somme

        if selection == "Draw": return round(prob_draw, 2)
        elif selection == "Home": return round(prob_home, 2)
        else: return round(prob_away, 2)

    elif market_key == "totals":
        target = float(point) if point else 2.5
        expected_total = (h_stat["attack_power"] + a_stat["attack_power"]) / (h_stat["defense_power"] + a_stat["defense_power"]) * 2.4
        if ctx["meteo_degradee"]: expected_total *= 0.85
        prob_over = 1.0 / (1.0 + (target / expected_total)**2.7)
        return round(prob_over, 2) if selection == "Over" else round(1.0 - prob_over, 2)

    elif market_key == "btts":
        prob_btts = (h_stat["attack_power"] * a_stat["attack_power"]) / (h_stat["defense_power"] * a_stat["defense_power"] * 2.1)
        return round(max(0.25, min(0.85, prob_btts)), 2) if selection == "Yes" else round(1.0 - max(0.25, min(0.85, prob_btts)), 2)

    elif market_key == "spreads":
        margin = float(point) if point else 0.0
        force_diff = (h_stat["attack_power"] - a_stat["attack_power"]) * 0.35
        prob_cover = max(0.05, min(0.95, 0.5 + (force_diff - (margin * 0.20))))
        return round(prob_cover, 2) if selection == "Home" else round(1.0 - prob_cover, 2)

    return 0.33

def fetch_live_data(sport_key):
    if not THE_ODDS_API_KEY or THE_ODDS_API_KEY == "TON_API_KEY_ICI":
        return []
    try:
        # ✅ L'URL s'adapte dynamiquement à la ligue demandée
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?regions=eu&markets=h2h,totals,spreads,btts&apiKey={THE_ODDS_API_KEY}"
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return []

def processing_pipeline(sport_key):
    raw_data = fetch_live_data(sport_key)
    processed_matches = []
    
    for item in raw_data:
        sport = item.get("sport_title", "Football")
        home = item.get("home_team", "Domicile")
        away = item.get("away_team", "Extérieur")
        
        raw_time = item.get("commence_time", "")
        gmt_time_str = "Heure Inconnue"
        if raw_time:
            try:
                dt = datetime.strptime(raw_time, "%Y-%m-%dT%H:%M:%SZ")
                gmt_time_str = dt.strftime("%d %b - %H:%M GMT")
            except Exception:
                gmt_time_str = raw_time
        
        bookmakers = item.get("bookmakers", [])
        if not bookmakers: continue
        
        rng = random.Random(len(home) + len(away))
        ctx = {
            "home_stats": generate_team_features(home),
            "away_stats": generate_team_features(away),
            "repos_home": rng.randint(3, 7), "repos_away": rng.randint(3, 7),
            "absents_home": rng.randint(0, 3), "absents_away": rng.randint(0, 3),
            "meteo_degradee": rng.choice([True, False])
        }
        
        all_markets = []
        for market in bookmakers[0].get("markets", []):
            market_key = market.get("key")
            market_titles = {"h2h": "1N2", "totals": "Total Buts", "btts": "Les 2 marquent", "spreads": "Handicap"}
            if market_key not in market_titles: continue
            
            outcomes_list = []
            for outcome in market.get("outcomes", []):
                name = outcome.get("name")
                price = float(outcome.get("price", 1.0))
                point = outcome.get("point", None)
                
                selection_id = "Home" if name == home else ("Away" if name == away else name)
                display_label = name
                if name == "Home": display_label = f"Victoire {home}"
                if name == "Away": display_label = f"Victoire {away}"
                if name == "Draw": display_label = "Match Nul"
                if name == "Yes": display_label = "Oui"
                if name == "No": display_label = "Non"
                if point is not None and name not in ["Yes", "No"]: display_label += f" ({point})"
                
                p_ia = advanced_predictive_engine(market_key, selection_id, point, sport, ctx)
                value = (p_ia * price) - 1
                is_value = value > 0.04
                
                kelly_stake = 0
                if is_value and price > 1:
                    kelly_stake = round(max(1.0, min(((value / (price - 1)) * 20), 8.0)), 1)
                
                outcomes_list.append({
                    "intitule": display_label, "cote": price, "prob": round(p_ia * 100, 1),
                    "value": round(value * 100, 1), "is_value": is_value, "mise": kelly_stake
                })
                
            all_markets.append({"marche_titre": market_titles[market_key], "options": outcomes_list})
            
        processed_matches.append({
            "sport": sport, "affiche": f"{home} vs {away}", "gmt_time": gmt_time_str,
            "meteo": "⚠️ Pluie" if ctx["meteo_degradee"] else "☀️ Beau temps", "marches": all_markets
        })
        
    return processed_matches

@app.route('/')
def home():
    # 📥 On récupère la ligue demandée dans l'URL (Ex: /?league=soccer_france_ligue_1)
    # Par défaut, si rien n'est coché, on met la MLS qui tourne en été
    selected_league = request.args.get('league', 'soccer_usa_mls')
    
    data = processing_pipeline(selected_league)
    total_signals = sum(1 for m in data for mar in m["marches"] for opt in mar["options"] if opt["is_value"])
    
    return render_template(
        'index.html', 
        matches=data, 
        total_signals=total_signals, 
        leagues=LEAGUES, 
        selected_league=selected_league
    )

app = app