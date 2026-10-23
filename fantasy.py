"""Roster + schedule analysis. Pure Python, no AI - the agent calls into this."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import nhl_api
import scoring

LEAGUES_DIR = Path(__file__).parent / 'leagues'
DEFAULT_LEAGUE = 'work'
INACTIVE_SLOTS = {'BN', 'IR', 'IR+'}
INJURED_SLOTS = {'IR', 'IR+'}


def available_leagues():
    return sorted(p.stem for p in LEAGUES_DIR.glob('*.json'))


def load_roster(league=DEFAULT_LEAGUE):
    """Roster, scoring and settings for one league, from leagues/<league>.json."""
    path = LEAGUES_DIR / f"{league}.json"
    if not path.exists():
        raise ValueError(f"Unknown league {league!r}. Available: {', '.join(available_leagues())}")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def team_label(roster):
    return roster.get('team_name') or roster.get('league', {}).get('name', 'My team')


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


def lineup_slots(players, roster_positions=None):
    """Active lineup slots, e.g. ['C1', 'C2', 'W1', ...].

    Taken from the league's roster_positions when it lists them, otherwise inferred from
    where players sit now.
    """
    if roster_positions:
        active = [s for s in roster_positions if s not in INACTIVE_SLOTS]
    else:
        order = ['C', 'LW', 'RW', 'W', 'Util', 'D', 'G']
        active = [slot for slot in order for p in players if p['position'] == slot]
    seen = {}
    slots = []
    for slot in active:
        seen[slot] = seen.get(slot, 0) + 1
        slots.append(f"{slot}{seen[slot]}")
    return slots


def optimal_lineup(playing, slots):
    """Highest expected-points lineup from players who have a game.

    Players are tried best-first (by expected points per game; current starters win ties)
    and a player only displaces someone by moving them to another slot. Because lineup
    slots form a matroid, this greedy order gives the maximum-value lineup, and it always
    fills as many slots as possible. Injured (IR/IR+) players are never used.
    Returns {slot: player}.
    """
    candidates = [p for p in playing if p['position'] not in INJURED_SLOTS]
    candidates.sort(key=lambda p: (-p.get('value', 0), p['position'] == 'BN'))
    assignment = {}

    def preferred_slots(player):
        # Current slot first, then natural positions, Util last - avoids pointless shuffling
        return sorted(slots, key=lambda s: (slot_type(s) != player['position'],
                                            slot_type(s) == 'Util'))

    def place(player, seen):
        options = [s for s in preferred_slots(player) if can_fill(player, slot_type(s))]
        # Take an open slot if there is one before bumping anyone
        for slot in options:
            if slot not in assignment and slot not in seen:
                assignment[slot] = player
                return True
        for slot in options:
            if slot in seen:
                continue
            seen.add(slot)
            if slot not in assignment or place(assignment[slot], seen):
                assignment[slot] = player
                return True
        return False

    for player in candidates:
        place(player, set())

    # Same players, re-seated with current starters placed first, so bench players fill
    # the open slots instead of bumping starters around (fewer moves to make in Yahoo)
    chosen = sorted(assignment.values(), key=lambda p: p['position'] == 'BN')
    assignment = {}
    for player in chosen:
        place(player, set())
    return assignment


def add_value(player, rules, week_days):
    """Attach expected fantasy points per game (and goalie start info) to a roster player."""
    team = nhl_api.normalize_team(player['nhl_team'])
    nhl = nhl_api.find_player(player['name'], team)
    if nhl is None:
        player.update(value=0.0, value_basis='player not found in NHL data')
        return
    if nhl['team'] and nhl['team'] != team:
        player['team_mismatch'] = f"NHL lists {player['name']} on {nhl['team']}"
    if 'G' in player['eligible_positions']:
        v = scoring.goalie_value(nhl['id'], team, rules)
        player['start_dates'] = scoring.goalie_start_dates(nhl['id'], week_days)
        player['starts_this_week'] = len(player['start_dates'])
        player['start_rate'] = v['start_rate']
    else:
        v = scoring.skater_value(nhl['id'], rules)
    player.update(value=v['value'], value_basis=v['basis'])


def drop_analysis(activate_name, league=DEFAULT_LEAGUE, weeks=4, today=None):
    """Who to drop to activate a player from IR.

    For each droppable player, projects total fantasy points over the next `weeks` weeks
    of the real NHL schedule (optimal lineup every day) with `activate_name` on the roster.
    """
    today = today or date.today()
    roster = load_roster(league)
    rules = roster.get('league', {}).get('scoring', {})
    players = [dict(p) for p in all_players(roster)]

    target = next((p for p in players if nhl_api.fold_name(p['name']) == nhl_api.fold_name(activate_name)),
                  None)
    if target is None:
        raise ValueError(f"{activate_name} is not on the roster")

    # Slots come from the current lineup, before anything moves
    slots = lineup_slots(players, roster.get('league', {}).get('roster_positions'))
    target['position'] = 'BN'

    first_week = nhl_api.week_start(today)
    days = [today + timedelta(days=i) for i in range(weeks * 7)]
    week_days = [(first_week + timedelta(days=i)).isoformat() for i in range(7)]
    game_dates = {}
    for w in range(weeks + 1):
        for team, games in nhl_api.get_week_schedule(first_week + timedelta(weeks=w)).items():
            game_dates.setdefault(team, set()).update(g['date'] for g in games)

    for p in players:
        add_value(p, rules, week_days)
        p['game_dates'] = sorted(game_dates.get(nhl_api.normalize_team(p['nhl_team']), ()))

    min_starts = roster.get('league', {}).get('min_goalie_starts_per_week') or 0

    def project(roster_players):
        """(total expected points, expected goalie starts per week) over the horizon."""
        total = starts = 0.0
        for day in days:
            playing = [p for p in roster_players if day.isoformat() in p['game_dates']]
            lineup = optimal_lineup(playing, slots).values()
            total += sum(p['value'] for p in lineup)
            starts += sum(p.get('start_rate', 0) for p in lineup)
        return total, starts / weeks

    candidates = [p for p in players if p is not target and p['position'] not in INJURED_SLOTS]
    results = []
    for drop in candidates:
        points, goalie_starts = project([p for p in players if p is not drop])
        results.append({
            'drop': drop['name'],
            'position': drop['position'],
            'eligible_positions': drop['eligible_positions'],
            'value_per_game': drop['value'],
            'projected_points': round(points, 1),
            'goalie_starts_per_week': round(goalie_starts, 1),
            'meets_goalie_minimum': round(goalie_starts, 1) >= min_starts,
        })
    # Options that keep the goalie minimum first, then by projected points
    results.sort(key=lambda r: (not r['meets_goalie_minimum'], -r['projected_points']))
    best = results[0]['projected_points']
    for r in results:
        r['points_lost_vs_best'] = round(best - r['projected_points'], 1)

    return {
        'league': roster.get('league', {}).get('name', league),
        'activate': target['name'],
        'activate_value_per_game': target['value'],
        'horizon': f"{days[0].isoformat()} to {days[-1].isoformat()}",
        'min_goalie_starts_per_week': min_starts,
        'options': results,
    }


def print_drop_report(activate_name, league=DEFAULT_LEAGUE):
    sys.stdout.reconfigure(encoding='utf-8')
    a = drop_analysis(activate_name, league)
    print(f"{a['league']}  |  Activating {a['activate']} ({a['activate_value_per_game']:.2f} pts/G)  |  "
          f"projection {a['horizon']}")
    print('-' * 64)
    print(f"{'Drop':<22} {'Slot':<5} {'Pts/G':>6} {'Team pts':>9} {'Lost':>6} {'G starts/wk':>12}")
    for r in a['options']:
        flag = '  below goalie minimum' if not r['meets_goalie_minimum'] else ''
        print(f"{r['drop']:<22} {r['position']:<5} {r['value_per_game']:>6.2f} "
              f"{r['projected_points']:>9.1f} {r['points_lost_vs_best']:>6.1f} "
              f"{r['goalie_starts_per_week']:>12.1f}{flag}")


def week_analysis(today=None, league=DEFAULT_LEAGUE):
    """Everything the agent needs to reason about this fantasy week."""
    today = today or date.today()
    start = nhl_api.week_start(today)
    schedule = nhl_api.get_week_schedule(start)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    roster = load_roster(league)
    settings = roster.get('league', {})
    rules = settings.get('scoring', {})

    players = []
    for p in all_players(roster):
        team = nhl_api.normalize_team(p['nhl_team'])
        games = schedule.get(team, [])
        player = {
            **p,
            'games_this_week': len(games),
            'games_remaining': sum(1 for g in games if g['date'] >= today.isoformat()),
            'game_dates': [g['date'] for g in games],
            'games': games,
        }
        add_value(player, rules, days)
        players.append(player)

    slots = lineup_slots(players, settings.get('roster_positions'))
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
            'projected_points': round(sum(p['value'] for p in lineup.values()
                                          if p in playing), 2),
            'current_lineup_projected_points': round(sum(p['value'] for p in current_starters
                                                         if p in playing), 2),
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

    # Goalie minimum: starts already made plus expected starts on remaining lineup days.
    # Today counts as remaining unless the game log already shows a start today.
    goalie_starts = {'minimum': settings.get('min_goalie_starts_per_week'), 'goalies': []}
    for g in players:
        if 'starts_this_week' not in g:
            continue
        logged = set(g.get('start_dates', []))
        lineup_days = [d['date'] for d in daily
                       if g['name'] in d['optimal_lineup'].values()
                       and d['date'] in g['game_dates'] and d['date'] not in logged]
        goalie_starts['goalies'].append({
            'name': g['name'],
            'starts_so_far': g['starts_this_week'],
            'expected_remaining': round(g['start_rate'] * len(lineup_days), 1),
        })
    goalie_starts['projected_total'] = round(sum(
        g['starts_so_far'] + g['expected_remaining'] for g in goalie_starts['goalies']), 1)

    return {
        'today': today.isoformat(),
        'week': f"{days[0]} to {days[-1]}",
        'team_name': team_label(roster),
        'league': settings,
        'players': players,
        'daily': daily,
        'goalie_starts': goalie_starts,
    }


def print_report(today=None, league=DEFAULT_LEAGUE):
    sys.stdout.reconfigure(encoding='utf-8')
    a = week_analysis(today, league)
    if not a['players']:
        print(f"No players in leagues/{league}.json yet - add the roster to get a report.")
        return
    print(f"{a['league'].get('name', league)}  |  {a['team_name']}  |  week {a['week']}  |  today {a['today']}")
    print('-' * 64)
    print(f"{'Slot':<5} {'Player':<22} {'Team':<4} {'Week':>4} {'Left':>4} {'Pts/G':>6}")
    for p in a['players']:
        print(f"{p['position']:<5} {p['name']:<22} {p['nhl_team']:<4} "
              f"{p['games_this_week']:>4} {p['games_remaining']:>4} {p['value']:>6.2f}"
              + (f"   ! {p['team_mismatch']}" if p.get('team_mismatch') else ''))
    for d in a['daily']:
        weekday = date.fromisoformat(d['date']).strftime('%a')
        print(f"\n{weekday} {d['date']}: {d['players_in_lineup_with_games']} with games, "
              f"{d['projected_points']:.1f} projected pts "
              f"(current lineup: {d['current_lineup_with_games']} games, "
              f"{d['current_lineup_projected_points']:.1f} pts)")
        if d['start']:
            print(f"  Start: {', '.join(d['start'])}   Bench: {', '.join(d['sit'])}")
        if d['moved_slots']:
            print(f"  Move:  {', '.join(d['moved_slots'])}")
        if not d['start'] and not d['moved_slots']:
            print("  No changes needed.")
        has_game = {p['name'] for p in a['players'] if d['date'] in p['game_dates']}
        value = {p['name']: p['value'] for p in a['players']}
        for slot, name in d['optimal_lineup'].items():
            if name is None:
                note = ''
            elif name in has_game:
                note = f"  {value[name]:.2f}"
            else:
                note = '  (no game)'
            print(f"  {slot_type(slot):<5} {name or '-'}{note}")
        if d['playing_but_no_slot']:
            print(f"  Playing but no room: {', '.join(d['playing_but_no_slot'])}")

    gs = a['goalie_starts']
    print("\nGoalie starts: " + ", ".join(
        f"{g['name']} {g['starts_so_far']} so far + ~{g['expected_remaining']}"
        for g in gs['goalies'])
        + f"  ->  ~{gs['projected_total']} projected"
        + (f" (minimum {gs['minimum']})" if gs['minimum'] else ''))


def pop_league_arg(args):
    """Remove `--league NAME` from an argv list and return NAME (or the default)."""
    if '--league' in args:
        i = args.index('--league')
        if i + 1 >= len(args):
            sys.exit(f"--league needs a name: {', '.join(available_leagues())}")
        league = args[i + 1]
        del args[i:i + 2]
        if league not in available_leagues():
            sys.exit(f"Unknown league {league!r}. Available: {', '.join(available_leagues())}")
        return league
    return DEFAULT_LEAGUE


if __name__ == '__main__':
    args = sys.argv[1:]
    league = pop_league_arg(args)
    if len(args) >= 2 and args[0] == 'drop':
        print_drop_report(' '.join(args[1:]), league)
    else:
        print_report(league=league)
