"""Edit a league file's roster (adds, drops, IR moves, eligibility) and commit the change.

The agent uses this to record moves the user reports, so the league files stay current
without editing them by hand. Only the player lists are rewritten; the league settings
above them are kept exactly as they are.
"""
import json
import subprocess
import sys
from collections import Counter

import fantasy
import nhl_api

GROUPS = ('forwards', 'defensemen', 'goalies')
INJURED = fantasy.INJURED_SLOTS
REPO = fantasy.LEAGUES_DIR.parent
TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def group_for(eligible):
    if 'G' in eligible:
        return 'goalies'
    return 'defensemen' if eligible == ['D'] else 'forwards'


def find(players, name):
    key = nhl_api.fold_name(name)
    matches = [p for p in players if nhl_api.fold_name(p['name']) == key]
    if not matches:  # last name alone, if it's unique
        matches = [p for p in players if nhl_api.fold_name(p['name']).split()[-1] == key]
    if len(matches) != 1:
        raise ValueError(f"{name!r} isn't on this roster" if not matches
                         else f"{name!r} matches more than one player")
    return matches[0]


def default_status(slot, current=None):
    if slot == 'BN':
        return 'Bench'
    if slot in INJURED:
        return current if current and current.startswith(('IR', 'O', 'DTD')) else 'IR'
    return 'Active'


def apply(data, change):
    """Apply one change to the league file's data; return a short description of it."""
    action = change['action']
    players = [p for g in GROUPS for p in data[g]]
    if action == 'add':
        name = change['name']
        if any(nhl_api.fold_name(p['name']) == nhl_api.fold_name(name) for p in players):
            raise ValueError(f"{name} is already on the roster")
        found = nhl_api.find_player(name, change.get('nhl_team') or None)
        eligible = change.get('eligible_positions') or (['G'] if found and found['position'] == 'G'
                                                         else None)
        if not eligible:
            raise ValueError(f"Give {name}'s Yahoo eligible_positions")
        team = change.get('nhl_team') or (found or {}).get('team')
        if not team:
            raise ValueError(f"Couldn't find {name}'s NHL team - give nhl_team")
        slot = change.get('slot') or 'BN'
        player = {'name': found['name'] if found else name, 'nhl_team': team, 'position': slot,
                  'eligible_positions': eligible,
                  'status': change.get('status') or default_status(slot)}
        data[group_for(eligible)].append(player)
        return f"added {player['name']} ({team}, {'/'.join(eligible)}) to {slot}"
    player = find(players, change['name'])
    if action == 'drop':
        for g in GROUPS:
            if player in data[g]:
                data[g].remove(player)
        return f"dropped {player['name']}"
    if action not in ('move', 'edit'):
        raise ValueError(f"Unknown action {action!r}")
    done = []
    if change.get('slot'):
        player['position'] = change['slot']
        player['status'] = change.get('status') or default_status(change['slot'], player['status'])
        done.append(f"to {change['slot']}")
    elif change.get('status'):
        player['status'] = change['status']
        done.append(f"status {change['status']}")
    if change.get('nhl_team'):
        player['nhl_team'] = change['nhl_team']
        done.append(f"team {change['nhl_team']}")
    if change.get('eligible_positions'):
        old = group_for(player['eligible_positions'])
        player['eligible_positions'] = change['eligible_positions']
        if group_for(player['eligible_positions']) != old:
            data[old].remove(player)
            data[group_for(player['eligible_positions'])].append(player)
        done.append(f"eligible {'/'.join(change['eligible_positions'])}")
    if not done:
        raise ValueError(f"Nothing to change for {player['name']}")
    return f"{player['name']}: {', '.join(done)}"


def check(data):
    """Raise if the roster no longer fits the league's slots."""
    slots = Counter(data['league']['roster_positions'])
    players = [p for g in GROUPS for p in data[g]]
    used = Counter(p['position'] for p in players)
    for slot in INJURED:
        if used[slot] > slots[slot]:
            raise ValueError(f"{used[slot]} players in {slot}, but the league has {slots[slot]} "
                             f"{slot} spot(s)")
    healthy = sum(n for s, n in used.items() if s not in INJURED)
    room = sum(n for s, n in slots.items() if s not in INJURED)
    if healthy > room:
        raise ValueError(f"{healthy} players outside IR, but only {room} active and bench spots "
                         "- drop someone or move someone to IR")
    unknown = set(used) - set(slots) - {'BN'}
    if unknown:
        raise ValueError(f"Unknown slot(s) {', '.join(sorted(unknown))}; this league has "
                         f"{', '.join(sorted(set(slots)))}")
    for p in players:
        slot = p['position']
        if slot not in INJURED and slot != 'BN' and not fantasy.can_fill(p, slot):
            raise ValueError(f"{p['name']} ({'/'.join(p['eligible_positions'])}) can't play {slot}")


def render(text, data):
    """The file's text with new player lists, keeping everything above them as it was."""
    start = text.index('  "forwards": [')
    blocks = []
    for g in GROUPS:
        rows = ',\n'.join('    ' + json.dumps(p, ensure_ascii=False) for p in data[g])
        blocks.append(f'  "{g}": [\n{rows}\n  ]')
    new = text[:start] + ',\n'.join(blocks) + '\n}\n'
    # Keep the file's line endings (Git on Windows may have checked it out with CRLF)
    return new.replace('\r\n', '\n').replace('\n', '\r\n') if '\r\n' in text else new


def git(*args):
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    return subprocess.run(['git', *args], cwd=REPO, capture_output=True, text=True, timeout=90,
                          creationflags=flags)


def update(league, changes, summary):
    """Apply `changes` to leagues/<league>.json, then commit and push just that file.

    All or nothing: if any change fails or the result doesn't fit the league's slots, the
    file is left alone. Returns what changed and whether the push went through.
    """
    path = fantasy.LEAGUES_DIR / f"{league}.json"
    text = path.read_text(encoding='utf-8')
    data = json.loads(text)
    done = [apply(data, c) for c in changes]
    check(data)
    new_text = render(text, data)
    json.loads(new_text)
    path.write_bytes(new_text.encode('utf-8'))

    result = {'league': league, 'changes': done}
    name = data['league'].get('name', league)
    relative = path.relative_to(REPO).as_posix()
    commit = git('commit', '-m', f"{name}: {summary}{TRAILER}", '--', relative)
    if commit.returncode != 0:
        result['commit'] = f"failed: {(commit.stderr or commit.stdout).strip()[-300:]}"
        return result
    push = git('push')
    result['commit'] = 'committed and pushed' if push.returncode == 0 else \
        f"committed, but the push failed: {push.stderr.strip()[-300:]}"
    return result
