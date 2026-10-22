"""Roster + schedule analysis. Pure Python, no AI - the agent calls into this."""
import json
import sys
from datetime import date, timedelta

import nhl_api

ROSTER_FILE = 'roster.json'
INACTIVE_SLOTS = {'BN', 'IR', 'IR+'}
INJURED_SLOTS = {'IR', 'IR+'}


def load_roster():
    with open(ROSTER_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def all_players(roster):
    return roster['forwards'] + roster['defensemen'] + roster['goalies']


def can_fill(player, slot):
    """Can `player` legally sit in lineup `slot`?"""
    eligible = player['eligible_positions']
    if slot == 'Util':
        return 'G' not in eligible
    return slot in eligible


def week_analysis(today=None):
    """Everything the agent needs to reason about this fantasy week."""
    today = today or date.today()
    start = nhl_api.week_start(today)
    schedule = nhl_api.get_week_schedule(start)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    roster = load_roster()

    players = []
    for p in all_players(roster):
        games = schedule.get(nhl_api.normalize_team(p['nhl_team']), [])
        players.append({
            **p,
            'games_this_week': len(games),
            'games_remaining': sum(1 for g in games if g['date'] >= today.isoformat()),
            'game_dates': [g['date'] for g in games],
            'games': games,
        })

    # Day by day: which starters are idle and which bench players could replace them
    daily = []
    for day in days:
        if day < today.isoformat():
            continue
        plays = {p['name'] for p in players if day in p['game_dates']}
        idle_starters = [p for p in players
                         if p['position'] not in INACTIVE_SLOTS and p['name'] not in plays]
        bench_playing = [p for p in players
                         if p['position'] == 'BN' and p['name'] in plays]
        swaps = []
        open_slots = list(idle_starters)
        for bench in bench_playing:
            for starter in open_slots:
                if can_fill(bench, starter['position']):
                    swaps.append({'start': bench['name'], 'sit': starter['name'],
                                  'slot': starter['position']})
                    open_slots.remove(starter)
                    break
        daily.append({
            'date': day,
            'starters_playing': sum(1 for p in players
                                    if p['position'] not in INACTIVE_SLOTS and p['name'] in plays),
            'idle_starters': [f"{p['name']} ({p['position']})" for p in idle_starters],
            'bench_playing': [p['name'] for p in bench_playing],
            'suggested_swaps': swaps,
        })

    return {
        'today': today.isoformat(),
        'week': f"{days[0]} to {days[-1]}",
        'team_name': roster['team_name'],
        'league': roster.get('league', {}),
        'players': players,
        'daily': daily,
    }


def print_report(today=None):
    sys.stdout.reconfigure(encoding='utf-8')
    a = week_analysis(today)
    print(f"{a['team_name']}  |  week {a['week']}  |  today {a['today']}")
    print('-' * 64)
    print(f"{'Slot':<5} {'Player':<22} {'Team':<4} {'Week':>4} {'Left':>4}")
    for p in a['players']:
        print(f"{p['position']:<5} {p['name']:<22} {p['nhl_team']:<4} "
              f"{p['games_this_week']:>4} {p['games_remaining']:>4}")
    print()
    for d in a['daily']:
        line = f"{d['date']}: {d['starters_playing']} starters playing"
        if d['suggested_swaps']:
            moves = '; '.join(f"start {s['start']} over {s['sit']} ({s['slot']})"
                              for s in d['suggested_swaps'])
            line += f"  ->  {moves}"
        print(line)


if __name__ == '__main__':
    print_report()
