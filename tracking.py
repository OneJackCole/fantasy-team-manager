"""Projection tracking: how close are the projections to what actually happens?

Each morning, update() scores the previous run's projections against the real box scores,
appends them to tracking/history.csv, and saves today's projections for scoring tomorrow.
The morning email job calls it, so the history builds up on its own.

Usage:
    python tracking.py              # accuracy summary from tracking/history.csv
"""
import csv
import json
import sys
from datetime import date
from pathlib import Path

import fantasy
import nhl_api
import scoring

TRACK_DIR = Path(__file__).parent / 'tracking'
PENDING_FILE = TRACK_DIR / 'pending.json'
HISTORY_FILE = TRACK_DIR / 'history.csv'
COLUMNS = ['date', 'league', 'player', 'nhl_team', 'group', 'projected', 'start_rate',
           'in_best_lineup', 'played', 'started', 'actual']


def projections(today, league):
    """Today's projection for every rostered player whose team plays today."""
    week = fantasy.week_analysis(today, league)
    plan = next((d for d in week['daily'] if d['date'] == today.isoformat()), None)
    best = set(plan['optimal_lineup'].values()) if plan else set()
    return [{
        'date': today.isoformat(),
        'league': league,
        'player': p['name'],
        'nhl_team': p['nhl_team'],
        'nhl_id': p['nhl_id'],
        'group': 'goalie' if 'G' in p['eligible_positions'] else 'skater',
        'projected': p['value'],
        'start_rate': p.get('start_rate', ''),
        'in_best_lineup': p['name'] in best,
    } for p in week['players']
        # IR players aren't expected to play, so their misses would only add noise
        if today.isoformat() in p['game_dates'] and p.get('nhl_id')
        and p['position'] not in fantasy.INJURED_SLOTS]


def score(entry):
    """A saved projection plus what actually happened."""
    rules = fantasy.load_roster(entry['league']).get('league', {}).get('scoring', {})
    stats = nhl_api.get_game_stats(entry['nhl_id'], entry['date'])
    row = {k: entry[k] for k in COLUMNS if k in entry}
    row['played'] = stats is not None
    row['started'] = bool(stats and stats.get('gamesStarted')) if entry['group'] == 'goalie' else ''
    row['actual'] = round(scoring.fantasy_points(
        stats, scoring.rules_for(rules, entry['group'] == 'goalie')), 2) if stats else 0
    return row


def update(today=None):
    """Score the saved projections (from an earlier day), then save today's."""
    today = today or date.today()
    TRACK_DIR.mkdir(exist_ok=True)
    if PENDING_FILE.exists():
        pending = json.loads(PENDING_FILE.read_text(encoding='utf-8'))
        if pending and pending[0]['date'] < today.isoformat():
            new_file = not HISTORY_FILE.exists()
            with open(HISTORY_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                if new_file:
                    writer.writeheader()
                for entry in pending:
                    writer.writerow(score(entry))
            print(f"Scored {len(pending)} projections from {pending[0]['date']}")
    today_entries = [e for league in fantasy.available_leagues()
                     if fantasy.all_players(fantasy.load_roster(league))
                     for e in projections(today, league)]
    PENDING_FILE.write_text(json.dumps(today_entries, indent=1, ensure_ascii=False), encoding='utf-8')
    print(f"Saved {len(today_entries)} projections for {today.isoformat()}")


def summary():
    """Accuracy by league and skaters/goalies: bias, typical miss, and goalie start rates."""
    if not HISTORY_FILE.exists():
        return "No history yet - it builds up from the daily email runs."
    with open(HISTORY_FILE, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    days = sorted({r['date'] for r in rows})
    out = [f"{len(rows)} projections over {len(days)} days ({days[0]} to {days[-1]})", ""]
    out.append(f"{'League':<16} {'Group':<7} {'N':>4} {'Proj/G':>7} {'Actual/G':>9} {'Bias':>6} "
               f"{'Avg miss':>9}")
    groups = {}
    for r in rows:
        groups.setdefault((r['league'], r['group']), []).append(r)
    for (league, group), rs in sorted(groups.items()):
        proj = [float(r['projected']) for r in rs]
        act = [float(r['actual']) for r in rs]
        n = len(rs)
        out.append(f"{league:<16} {group:<7} {n:>4} {sum(proj) / n:>7.2f} {sum(act) / n:>9.2f} "
                   f"{(sum(act) - sum(proj)) / n:>+6.2f} "
                   f"{sum(abs(a - p) for a, p in zip(act, proj)) / n:>9.2f}")
        if group == 'goalie':
            rate = sum(float(r['start_rate'] or 0) for r in rs) / n
            started = sum(r['started'] == 'True' for r in rs) / n
            out.append(f"{'':<16} {'':<7} start rate projected {rate:.0%}, actual {started:.0%}")
    out.append("\nBias = actual minus projected per game (positive: projections too low).")
    out.append("A single night is noisy; trust these after a few weeks of data.")
    return '\n'.join(out)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print(summary())
