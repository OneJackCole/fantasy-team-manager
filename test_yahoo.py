"""One-time Yahoo sign-in, then a check that your teams are visible.

Before the first run, create oauth2.json (git-ignored) with your Yahoo app's credentials:
    {"consumer_key": "...", "consumer_secret": "..."}
Running this opens Yahoo's sign-in and saves the tokens into the same file. Once it connects,
it marks the file "enabled", which switches live Yahoo rosters on for local runs.
"""
import json

from yahoo_oauth import OAuth2

import fantasy
import yahoo_api


def test_connection():
    sc = OAuth2(None, None, from_file='oauth2.json')
    if not sc.token_is_valid():
        sc.refresh_access_token()

    yahoo_api.enable()
    teams = yahoo_api.my_teams()
    yahoo_api.mark_enabled()
    print("Connected - live Yahoo rosters are now on for local runs. Your Yahoo NHL teams:")
    for t in teams:
        print(f"  {t['name']}")

    # Each league file's team_name must match a Yahoo team for live rosters
    names = {t['name'].strip().lower() for t in teams}
    for league in fantasy.available_leagues():
        with open(fantasy.LEAGUES_DIR / f"{league}.json", encoding='utf-8') as f:
            team_name = json.load(f).get('team_name', '')
        status = 'matched' if team_name.strip().lower() in names else 'NOT FOUND - fix team_name'
        print(f"leagues/{league}.json team_name {team_name!r}: {status}")


if __name__ == '__main__':
    test_connection()
