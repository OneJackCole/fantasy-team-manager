"""Thin client for the public (unauthenticated) NHL APIs."""
import unicodedata
from datetime import date, timedelta
from functools import lru_cache

import requests

WEB_API = "https://api-web.nhle.com/v1"
SEARCH_API = "https://search.d3.nhle.com/api/v1/search/player"

# Yahoo-style abbreviations -> NHL API abbreviations
TEAM_ALIASES = {
    'TB': 'TBL',
    'NJ': 'NJD',
    'LA': 'LAK',
    'SJ': 'SJS',
}


def normalize_team(abbrev):
    return TEAM_ALIASES.get(abbrev, abbrev)


def _fold(name):
    """Lowercase and strip accents so 'Lafrenière' matches 'Lafreniere'."""
    decomposed = unicodedata.normalize('NFKD', name)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


def _get(url, **params):
    response = requests.get(url, params=params or None, timeout=15)
    response.raise_for_status()
    return response.json()


def week_start(day=None):
    """Monday of the fantasy week containing `day` (Yahoo weeks run Mon-Sun)."""
    day = day or date.today()
    return day - timedelta(days=day.weekday())


@lru_cache(maxsize=8)
def get_week_schedule(start):
    """Games for the 7 days starting at `start` (a date).

    Returns {team_abbrev: [{'date', 'opponent', 'home'}...]}.
    """
    data = _get(f"{WEB_API}/schedule/{start.isoformat()}")
    games_by_team = {}
    for game_day in data.get('gameWeek', []):
        for game in game_day.get('games', []):
            away = game['awayTeam']['abbrev']
            home = game['homeTeam']['abbrev']
            games_by_team.setdefault(away, []).append(
                {'date': game_day['date'], 'opponent': home, 'home': False})
            games_by_team.setdefault(home, []).append(
                {'date': game_day['date'], 'opponent': away, 'home': True})
    return games_by_team


@lru_cache(maxsize=64)
def find_player(name, team_hint=None):
    """Look up a player by name. Returns {'id', 'name', 'team', 'position'} or None.

    `team_hint` breaks ties between players with the same name.
    """
    results = _get(SEARCH_API, culture='en-us', limit=20, q=name, active='true')
    matches = [r for r in results if _fold(r['name']) == _fold(name)]
    if not matches:
        return None
    if team_hint and len(matches) > 1:
        hinted = [r for r in matches if r.get('teamAbbrev') == normalize_team(team_hint)]
        matches = hinted or matches
    best = matches[0]
    return {
        'id': int(best['playerId']),
        'name': best['name'],
        'team': best.get('teamAbbrev'),
        'position': best.get('positionCode'),
    }


@lru_cache(maxsize=64)
def get_player_stats(player_id):
    """Current-season stats and the last 5 games for a player."""
    data = _get(f"{WEB_API}/player/{player_id}/landing")
    featured = data.get('featuredStats', {})
    return {
        'name': f"{data['firstName']['default']} {data['lastName']['default']}",
        'team': data.get('currentTeamAbbrev'),
        'position': data.get('position'),
        'season': featured.get('season'),
        'season_stats': featured.get('regularSeason', {}).get('subSeason', {}),
        'last_5_games': data.get('last5Games', []),
    }
