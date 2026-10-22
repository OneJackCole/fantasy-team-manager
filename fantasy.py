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


def slot_type(slot):
    """'Util2' -> 'Util'"""
    return slot.rstrip('0123456789')


def lineup_slots(players):
    """Active lineup slots, inferred from where players sit now, e.g. ['C1', 'C2', 'W1', ...]."""
    order = ['C', 'W', 'Util', 'D', 'G']
    counts = {slot: sum(1 for p in players if p['position'] == slot) for slot in order}
    return [f"{slot}{i + 1}" for slot in order for i in range(counts[slot])]


def optimal_lineup(playing, slots):
    """Fill as many slots as possible with players who have a game.

    Players are tried in priority order (current starters first, then bench, in roster
    order) and a player only displaces someone by moving them to another slot, so the
    result is the largest possible lineup that keeps the highest-priority players.
    Injured (IR/IR+) players are never used. Returns {slot: player}.
    """
    candidates = [p for p in playing if p['position'] not in INJURED_SLOTS]
    candidates.sort(key=lambda p: p['position'] == 'BN')
    assignment = {}

    def preferred_slots(player):
        # Current slot first, then natural positions, Util last - avoids pointless shuffling
        return sorted(slots, key=lambda s: (slot_type(s) != player['position'],
                                            slot_type(s) == 'Util'))

    def place(player, seen):
        for slot in preferred_slots(player):
            if slot in seen or not can_fill(player, slot_type(slot)):
                continue
            seen.add(slot)
            if slot not in assignment or place(assignment[slot], seen):
                assignment[slot] = player
                return True
        return False

    for player in candidates:
        place(player, set())
    return assignment


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

    slots = lineup_slots(players)
    daily = []
    for day in days:
        if day < today.isoformat():
            continue
        playing = [p for p in players if day in p['game_dates']]
        lineup = optimal_lineup(playing, slots)
        players_with_games = len(lineup)
        current_starters = [p for p in players if p['position'] not in INACTIVE_SLOTS]

        # Idle starters keep their spot unless someone with a game needed it
        for starter in current_starters:
            if starter in lineup.values():
                continue
            free = [s for s in slots if s not in lineup and slot_type(s) == starter['position']]
            if free:
                lineup[free[0]] = starter

        in_lineup = {p['name'] for p in lineup.values()}
        daily.append({
            'date': day,
            'players_in_lineup_with_games': players_with_games,
            'current_lineup_with_games': sum(1 for p in current_starters if p in playing),
            'optimal_lineup': {slot: lineup[slot]['name'] if slot in lineup else None
                               for slot in slots},
            'start': [p['name'] for p in players
                      if p['position'] == 'BN' and p['name'] in in_lineup],
            'sit': [p['name'] for p in current_starters if p['name'] not in in_lineup],
            'moved_slots': [f"{p['name']} {p['position']}->{slot_type(s)}"
                            for s, p in lineup.items()
                            if p['position'] not in ('BN', slot_type(s))],
            'playing_but_no_slot': [p['name'] for p in playing
                                    if p['name'] not in in_lineup
                                    and p['position'] not in INJURED_SLOTS],
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
    for d in a['daily']:
        weekday = date.fromisoformat(d['date']).strftime('%a')
        print(f"\n{weekday} {d['date']}: {d['players_in_lineup_with_games']} with games "
              f"(current lineup: {d['current_lineup_with_games']})")
        if d['start']:
            print(f"  Start: {', '.join(d['start'])}   Bench: {', '.join(d['sit'])}")
        if d['moved_slots']:
            print(f"  Move:  {', '.join(d['moved_slots'])}")
        if not d['start'] and not d['moved_slots']:
            print("  No changes needed.")
        has_game = {p['name'] for p in a['players'] if d['date'] in p['game_dates']}
        for slot, name in d['optimal_lineup'].items():
            note = '' if name is None or name in has_game else '  (no game)'
            print(f"  {slot_type(slot):<5} {name or '-'}{note}")
        if d['playing_but_no_slot']:
            print(f"  Playing but no room: {', '.join(d['playing_but_no_slot'])}")


if __name__ == '__main__':
    print_report()
