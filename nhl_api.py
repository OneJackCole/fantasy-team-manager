"""Thin client for the public (unauthenticated) NHL APIs."""
import time
import unicodedata
from datetime import date, timedelta
from functools import lru_cache

import requests

WEB_API = "https://api-web.nhle.com/v1"
SEARCH_API = "https://search.d3.nhle.com/api/v1/search/player"
STATS_API = "https://api.nhle.com/stats/rest/en"

# Numeric fields in season rows that are identifiers, not stats to add up across teams
ID_FIELDS = {'season', 'sequence', 'gameTypeId'}

# Skater stats only the stats API has, merged in by get_realtime_totals
REALTIME_STATS = ('hits', 'blockedShots')

# Yahoo-style abbreviations -> NHL API abbreviations
TEAM_ALIASES = {
    'TB': 'TBL',
    'NJ': 'NJD',
    'LA': 'LAK',
    'SJ': 'SJS',
}


def normalize_team(abbrev):
    return TEAM_ALIASES.get(abbrev, abbrev)


def fold_name(name):
    """Lowercase and strip accents so 'Lafrenière' matches 'Lafreniere'."""
    decomposed = unicodedata.normalize('NFKD', name)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


def _get(url, **params):
    for attempt in range(4):
        response = requests.get(url, params=params or None, timeout=15)
        if response.status_code != 429:
            break
        # Rate limited - back off and retry
        time.sleep(int(response.headers.get('Retry-After', 2 ** (attempt + 1))))
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
    matches = [r for r in results if fold_name(r['name']) == fold_name(name)]
    first, _, last = name.partition(' ')
    if not matches and first.endswith('.') and last:
        # Yahoo abbreviates long names: "M. Brandsegg-Nygård"
        results = _get(SEARCH_API, culture='en-us', limit=20, q=last, active='true')
        matches = [r for r in results
                   if fold_name(r['name']).endswith(' ' + fold_name(last))
                   and fold_name(r['name']).startswith(fold_name(first[:-1]))]
    if not matches and team_hint and last:
        # Nicknames ("Freddy" Gaudreau is Frederick): same last name on the same team
        results = _get(SEARCH_API, culture='en-us', limit=20, q=last, active='true')
        matches = [r for r in results
                   if fold_name(r['name']).endswith(' ' + fold_name(last))
                   and r.get('teamAbbrev') == normalize_team(team_hint)]
        if len(matches) > 1:
            matches = []
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
def _landing(player_id):
    return _get(f"{WEB_API}/player/{player_id}/landing")


def get_season_totals(player_id):
    """NHL regular-season totals by season id, e.g. {20262027: {...}, 20252026: {...}}.

    Seasons split across teams (trades) are summed.
    """
    totals = {}
    for row in _landing(player_id).get('seasonTotals', []):
        if row.get('leagueAbbrev') != 'NHL' or row.get('gameTypeId') != 2:
            continue
        season = totals.setdefault(row['season'], {'season': row['season']})
        for key, value in row.items():
            if key in ID_FIELDS:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                season[key] = season.get(key, 0) + value
    for season in totals.values():
        if 'shotsAgainst' in season:
            season['saves'] = season['shotsAgainst'] - season.get('goalsAgainst', 0)
    return totals


@lru_cache(maxsize=64)
def get_realtime_totals(player_id):
    """Hits and blocked shots by season id - the player endpoints don't carry them."""
    data = _get(f"{STATS_API}/skater/realtime", isAggregate='false', isGame='false',
                cayenneExp=f"playerId={player_id} and gameTypeId=2")
    totals = {}
    for row in data.get('data', []):
        season = totals.setdefault(row['seasonId'], {})
        for key in REALTIME_STATS:
            season[key] = season.get(key, 0) + (row.get(key) or 0)
    return totals


@lru_cache(maxsize=64)
def get_game_log(player_id):
    """This season's regular-season game log, most recent first."""
    return _get(f"{WEB_API}/player/{player_id}/game-log/now").get('gameLog', [])


@lru_cache(maxsize=32)
def get_boxscore(game_id):
    return _get(f"{WEB_API}/gamecenter/{game_id}/boxscore")


def get_game_stats(player_id, day):
    """A player's stats for his game on `day` (ISO date), keyed like season totals, or None.

    The game log has most stats; hits and blocks come from the box score.
    """
    game = next((g for g in get_game_log(player_id) if g.get('gameDate') == day), None)
    if game is None:
        return None
    stats = {k: v for k, v in game.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool)}
    stats['gamesPlayed'] = 1
    if 'shotsAgainst' in stats:
        stats['saves'] = stats['shotsAgainst'] - stats.get('goalsAgainst', 0)
        stats['wins'] = int(game.get('decision') == 'W')
    else:
        teams = get_boxscore(game['gameId']).get('playerByGameStats', {}).values()
        for row in (r for team in teams for group in ('forwards', 'defense') for r in team.get(group, [])):
            if row.get('playerId') == player_id:
                stats.update({k: row.get(k, 0) for k in REALTIME_STATS})
    return stats


@lru_cache(maxsize=1)
def get_team_games_played():
    """{team_abbrev: games played this season} from the current standings."""
    data = _get(f"{WEB_API}/standings/now")
    return {row['teamAbbrev']['default']: row['gamesPlayed'] for row in data['standings']}


def current_season(day=None):
    """Season id for `day`, e.g. 20262027. Seasons start in September.

    Not taken from a player's featured stats: for a player who hasn't played yet this
    season (injured), those still point at last season.
    """
    day = day or date.today()
    start = day.year if day.month >= 9 else day.year - 1
    return int(f"{start}{start + 1}")


def get_player_stats(player_id):
    """Current-season stats and the last 5 games for a player."""
    data = _landing(player_id)
    featured = data.get('featuredStats', {})
    return {
        'name': f"{data['firstName']['default']} {data['lastName']['default']}",
        'team': data.get('currentTeamAbbrev'),
        'position': data.get('position'),
        'season': featured.get('season'),
        'season_stats': featured.get('regularSeason', {}).get('subSeason', {}),
        'last_5_games': data.get('last5Games', []),
    }
