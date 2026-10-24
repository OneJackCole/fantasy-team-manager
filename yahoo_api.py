"""Read-only Yahoo Fantasy API client: your teams' rosters and lineups.

Data is fetched live on every run and kept in memory only - nothing from Yahoo is written
to disk or committed. Fantasy data provided by Yahoo Fantasy.

Credentials, from environment variables (GitHub Actions) or oauth2.json (local, git-ignored,
created by `python test_yahoo.py`):
    YAHOO_CONSUMER_KEY / consumer_key
    YAHOO_CONSUMER_SECRET / consumer_secret
    YAHOO_REFRESH_TOKEN / refresh_token
"""
import json
import os
import time
from functools import lru_cache
from pathlib import Path

import requests

FANTASY_API = "https://fantasy.yahooapis.com/fantasy/v2"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
OAUTH_FILE = Path(__file__).parent / 'oauth2.json'
# Must match the app's Redirect URI on the Yahoo Developer page exactly. Nothing listens
# there: after sign-in the browser fails to load it, and the code is in its address bar.
REDIRECT_URI = 'https://localhost:8080'
ATTRIBUTION = "Fantasy data provided by Yahoo Fantasy"
ATTRIBUTION_URL = "https://sports.yahoo.com/fantasy/"

# Positions that can fill a lineup slot; Yahoo's eligible lists also carry Util, IR, etc.
PLAYING_POSITIONS = ('C', 'LW', 'RW', 'W', 'D', 'G')


# Set by enable() during the one-time connection check, before oauth2.json is marked enabled
_enabled_for_setup = False


def _credentials():
    env = [os.environ.get(k) for k in
           ('YAHOO_CONSUMER_KEY', 'YAHOO_CONSUMER_SECRET', 'YAHOO_REFRESH_TOKEN')]
    if all(env):
        return env
    if OAUTH_FILE.exists():
        data = json.loads(OAUTH_FILE.read_text(encoding='utf-8'))
        values = [data.get(k) for k in ('consumer_key', 'consumer_secret', 'refresh_token')]
        # Tokens alone don't switch Yahoo on locally: test_yahoo.py marks the file "enabled"
        # once a connection succeeds, so nothing calls Yahoo before you choose to
        if all(values) and (data.get('enabled') or _enabled_for_setup):
            return values
    return None


def enable():
    """Use oauth2.json's credentials even before it's marked enabled (for test_yahoo.py)."""
    global _enabled_for_setup
    _enabled_for_setup = True


def mark_enabled():
    """Switch live Yahoo rosters on for local runs."""
    data = json.loads(OAUTH_FILE.read_text(encoding='utf-8'))
    data['enabled'] = True
    OAUTH_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


def available():
    """Are Yahoo credentials set up? Without them, rosters come from the league files."""
    return _credentials() is not None


@lru_cache(maxsize=1)
def _access_token():
    key, secret, refresh = _credentials()
    response = requests.post(TOKEN_URL, auth=(key, secret), timeout=15, data={
        'grant_type': 'refresh_token', 'refresh_token': refresh, 'redirect_uri': REDIRECT_URI})
    response.raise_for_status()
    return response.json()['access_token']


def _get(path):
    for attempt in range(4):
        response = requests.get(f"{FANTASY_API}/{path}", params={'format': 'json'}, timeout=20,
                                headers={'Authorization': f"Bearer {_access_token()}"})
        # Yahoo signals rate limiting with 429 or its own 999
        if response.status_code not in (429, 999):
            break
        time.sleep(2 ** (attempt + 1))
    response.raise_for_status()
    return response.json()['fantasy_content']


def _merge(items):
    """Yahoo's JSON splits one object across a list of single-key dicts; join them."""
    merged = {}
    for item in items if isinstance(items, list) else [items]:
        if isinstance(item, dict):
            merged.update(item)
        elif isinstance(item, list):
            merged.update(_merge(item))
    return merged


def _find(node, key):
    """Every value stored under `key`, anywhere in a Yahoo response."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            else:
                yield from _find(v, key)
    elif isinstance(node, list):
        for item in node:
            yield from _find(item, key)


@lru_cache(maxsize=1)
def my_teams():
    """Your NHL fantasy teams this season: [{'team_key', 'name'}]."""
    data = _get("users;use_login=1/games;game_keys=nhl/teams")
    teams = [_merge(t) for t in _find(data, 'team')]
    return [{'team_key': t['team_key'], 'name': t['name']} for t in teams if 'team_key' in t]


def team_key(team_name):
    """The team key for one of your teams, matched by name (case-insensitive)."""
    teams = my_teams()
    match = [t for t in teams if t['name'].strip().lower() == team_name.strip().lower()]
    if not match:
        raise ValueError(f"No Yahoo team named {team_name!r} - check team_name in the league "
                         f"file (your teams: {', '.join(t['name'] for t in teams)})")
    return match[0]['team_key']


def _player(entry):
    """One roster entry in the league-file format."""
    meta = _merge(entry[0]) if entry and isinstance(entry[0], list) else _merge(entry)
    extra = _merge(entry[1:]) if len(entry) > 1 else {}
    eligible = [p['position'] for p in meta.get('eligible_positions', [])
                if isinstance(p, dict) and p.get('position') in PLAYING_POSITIONS]
    selected = _merge(extra.get('selected_position', [])).get('position', 'BN')
    return {
        'name': meta['name']['full'],
        'nhl_team': meta.get('editorial_team_abbr', '').upper(),
        'position': selected,
        'eligible_positions': eligible,
        'status': meta.get('status') or ('Bench' if selected == 'BN' else 'Active'),
    }


@lru_cache(maxsize=16)
def roster(key, day):
    """A team's players and lineup slots on `day` (ISO date), grouped like the league files."""
    data = _get(f"team/{key}/roster;date={day}/players")
    players = [_player(p) for p in _find(data, 'player') if isinstance(p, list)]
    return {
        'forwards': [p for p in players if 'G' not in p['eligible_positions']
                     and 'D' not in p['eligible_positions']],
        'defensemen': [p for p in players if 'D' in p['eligible_positions']],
        'goalies': [p for p in players if 'G' in p['eligible_positions']],
    }
