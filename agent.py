"""Fantasy hockey agent: Claude + NHL data tools.

Runs through Claude Code (the Claude Agent SDK), so it uses your Claude subscription rather
than API billing. Needs Claude Code installed and logged in, or CLAUDE_CODE_OAUTH_TOKEN set.

Usage:
    python agent.py                      # daily briefing for every league
    python agent.py "Should I start Forsberg tonight?"
    python agent.py --league pushing_pucks "Who should I stream at D?"
"""
import asyncio
import json
import statistics
import sys
from datetime import date

from claude_agent_sdk import (ClaudeAgentOptions, ClaudeSDKError, CLINotFoundError, ResultMessage,
                              create_sdk_mcp_server, query, tool)

import fantasy
import nhl_api
import scoring

MODEL = "claude-opus-5-5"

# The league tools use when a call doesn't name one; run() sets it when the user picked one
LEAGUE = fantasy.DEFAULT_LEAGUE

SYSTEM_PROMPT = """You are a fantasy hockey assistant managing a Yahoo fantasy hockey team \
for the user. You give concise, practical advice: who to start, who to sit, and where the \
schedule creates an edge. The league's name, format, scoring rules, roster positions, goalie \
minimum, acquisition limits, and other notes are in the `league` field of get_week_overview. \
Scoring differs a lot between leagues (some reward hits, blocks, shots, and saves, and penalize \
goals against), so reason from that league's rules, not generic fantasy value.

How to work:
- Start with get_week_overview. Each player has a `value`: expected fantasy points per game
  (goalies: per team game, factoring in how often they start), blending this season with a
  prior healthy season. Each remaining day has the lineup that maximizes total value, and there
  is a goalie-starts projection against the weekly minimum. Treat those lineups as the baseline;
  override only with a concrete reason (hot or cold streak, role change, likely goalie rest day).
- Pull get_player_stats (with the league) for players whose start/sit call is close, or whose
  recent form matters: it scores each game this season under that league's rules, hits and
  blocks included. Treat `value` as the best estimate of a player's average: in a backtest on
  2025-26, leaning more on early-season numbers (even with an ice-time change) predicted the
  rest of the season worse than `value`. Use the per-game data for ceiling and consistency
  (when the user needs upside, a higher-variance player can be the better start) and for
  role changes you can name (a promotion to the top line or power play, an injury above him).
- Use get_team_game_counts when suggesting pickups or streamers by schedule.
- When a player comes off IR, use evaluate_drop_candidates to decide who to drop.
- When the user lists free agents, use evaluate_pickups to rank them and pick drops.
- Players in BN can be swapped into an active slot only if their eligible_positions fit that slot \
(Util, where the league has it, takes any skater). Players in IR/IR+ slots are injured and can't \
play.
- If the league has max_acquisitions_per_week, keep pickup suggestions within it.

Limits you must respect:
- You do NOT know which players are free agents in the user's league. When you suggest a pickup, \
frame it as "if available" and suggest checking the waiver wire.
- You have no injury news or confirmed starting goalies beyond what the stats show. Say so when \
it matters (especially for goalie starts).
- If a player's NHL team in the stats differs from the roster file, point it out - they were \
probably traded and the league's roster file needs updating. (Yahoo and NHL abbreviations like NJ/NJD and \
TB/TBL are the same team - don't flag those.)

Format: plain text for a terminal. Lead with the actionable moves for today, then the rest of \
the week, then anything worth watching. Keep it short."""

TOOLS = []

LEAGUE_ARG = {"league": {"type": "string", "enum": fantasy.available_leagues(),
                         "description": "League key from the league list in your instructions."}}


def league_of(args):
    league = args.get('league') or LEAGUE
    if league not in fantasy.available_leagues():
        raise ValueError(f"Unknown league {league!r}")
    return league


def fantasy_tool(name, description, input_schema):
    """Register fn(args) -> JSON string as a tool for the agent."""
    def register(fn):
        async def handler(args):
            return {"content": [{"type": "text", "text": fn(args)}]}
        TOOLS.append(tool(name, description, input_schema)(handler))
        return fn
    return register


@fantasy_tool(
    "get_week_overview",
    "Get the user's fantasy roster with this week's NHL schedule. Returns each player's lineup "
    "slot, eligible positions, NHL team, games this week and games remaining, and expected "
    "fantasy points per game (`value`). For each remaining day: the lineup with the highest "
    "expected points, which bench players to start, which starters to bench, slot moves, and "
    "projected points vs. the current lineup. Also projects goalie starts for the week against "
    "the league minimum.",
    {"type": "object", "properties": LEAGUE_ARG, "required": ["league"]})
def get_week_overview(args):
    return json.dumps(fantasy.week_analysis(league=league_of(args)))


@fantasy_tool(
    "get_player_stats",
    "Get current-season stats and the last 5 games for an NHL player. For skaters, also every "
    "game this season with hits, blocks and ice time, and - given a league - each game's "
    "fantasy points under that league's scoring, with the season average, spread and best "
    "games. Use these to judge recent form and ceiling, which the blended `value` smooths over.",
    {"type": "object",
     "properties": {
         "name": {"type": "string", "description": "Player's full name, e.g. \"Filip Forsberg\"."},
         "team": {"type": "string", "description": "Optional NHL team abbreviation to "
                                                   "disambiguate players with the same name."},
         **LEAGUE_ARG},
     "required": ["name"]})
def get_player_stats(args):
    name = args['name']
    player = nhl_api.find_player(name, args.get('team') or None)
    if player is None:
        return json.dumps({'error': f"No active NHL player found named {name!r}"})
    stats = nhl_api.get_player_stats(player['id'])
    if player['position'] != 'G':
        games = nhl_api.get_skater_games(player['id'])
        if args.get('league'):
            rules = scoring.rules_for(
                fantasy.load_roster(league_of(args))['league'].get('scoring', {}), goalie=False)
            for g in games:
                g['fantasy_points'] = round(scoring.fantasy_points(g, rules), 1)
            pts = sorted(g['fantasy_points'] for g in games)
            if pts:
                stats['fantasy_this_season'] = {
                    'league': args['league'], 'games': len(pts),
                    'average': round(sum(pts) / len(pts), 2),
                    'spread_sd': round(statistics.pstdev(pts), 2),
                    'best_games': pts[-3:][::-1],
                    'share_of_games_15_plus': round(sum(p >= 15 for p in pts) / len(pts), 2)}
        stats['games_this_season'] = games
    return json.dumps(stats)


@fantasy_tool(
    "evaluate_drop_candidates",
    "Rank who to drop to make room for a player coming off IR. For each droppable player, "
    "projects the team's total fantasy points over the next 4 weeks of the real NHL schedule "
    "(best lineup every day) and expected goalie starts per week. Options that would fall below "
    "the weekly goalie-start minimum are ranked last.",
    {"type": "object",
     "properties": {
         "activate_player": {"type": "string", "description": "Name of the rostered IR player "
                                                              "being activated, e.g. \"Seth Jarvis\"."},
         **LEAGUE_ARG},
     "required": ["activate_player", "league"]})
def evaluate_drop_candidates(args):
    try:
        return json.dumps(fantasy.drop_analysis(args['activate_player'], league_of(args)))
    except ValueError as e:
        return json.dumps({'error': str(e)})


@fantasy_tool(
    "evaluate_pickups",
    "Rank free agents by how much each would add, and who to drop for each. For each player, "
    "projects the team's total fantasy points over the next 4 weeks with them added in place of "
    "each droppable player, keeps the best swap, and reports the gain over the current roster "
    "and expected goalie starts per week.",
    {"type": "object",
     "properties": {
         "players": {"type": "string", "description": "Free agents, one per line as "
                                                      "\"Name TEAM - POS,POS\", e.g. \"Jack Roslovic "
                                                      "TOR - C,RW\". Text copied from a Yahoo player "
                                                      "list also works."},
         **LEAGUE_ARG},
     "required": ["players", "league"]})
def evaluate_pickups(args):
    candidates = fantasy.parse_players(args['players'])
    if not candidates:
        return json.dumps({'error': "No players found. Use lines like 'Jack Roslovic TOR - C,RW'."})
    return json.dumps(fantasy.pickup_analysis(candidates, league_of(args)))


@fantasy_tool(
    "get_team_game_counts",
    "Get how many games every NHL team plays this fantasy week (Mon-Sun) and how many remain "
    "from today, sorted by games remaining. Useful for finding streaming targets.",
    {})
def get_team_game_counts(args):
    today = date.today().isoformat()
    schedule = nhl_api.get_week_schedule(nhl_api.week_start())
    counts = [{
        'team': team,
        'games_this_week': len(games),
        'games_remaining': sum(1 for g in games if g['date'] >= today),
        'remaining_dates': [g['date'] for g in games if g['date'] >= today],
    } for team, games in schedule.items()]
    counts.sort(key=lambda c: c['games_remaining'], reverse=True)
    return json.dumps(counts)


DISCORD_FORMAT = """

This reply goes to Discord, not a terminal: light Markdown (bold, short bullet lists) is fine, \
no tables or headings, and keep it under 1,800 characters."""


def league_directory():
    """Each league's key, name, team and rostered players, so the agent can tell from the
    players a question mentions which league it's about."""
    lines = ["The user manages one team in each of these leagues. Pass the league key to the "
             "tools that take one:"]
    for key in fantasy.available_leagues():
        roster = fantasy.load_roster(key)
        players = ', '.join(f"{p['name']} ({p['position']})" for p in fantasy.all_players(roster))
        lines.append(f"- {key}: {roster['league'].get('name', key)}, team "
                     f"\"{fantasy.team_label(roster)}\". Roster: {players}")
    return '\n'.join(lines)


async def run(question, league=None, discord=False, history=()):
    """Answer `question`. With `league`, it's about that league; otherwise the agent works out
    which league from the players mentioned. `history` is earlier (question, answer) pairs
    from the same conversation. One question at a time: tools default to the module LEAGUE."""
    global LEAGUE
    LEAGUE = league or fantasy.DEFAULT_LEAGUE
    if league:
        scope = f"The user is asking about the {league} league."
    else:
        scope = ("The user didn't say which league. Work it out from the players they mention: "
                 "use the league whose roster has them. If they're on rosters in more than one "
                 "league, or a general question applies to both, cover each league separately "
                 "and label it. Never apply one league's roster or scoring to another.")
    system_prompt = '\n\n'.join([SYSTEM_PROMPT, league_directory(), scope])
    if history:
        earlier = '\n\n'.join(f"User: {q}\nYou: {a}" for q, a in history)
        question = f"Earlier in this conversation:\n\n{earlier}\n\nNew question: {question}"
    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=system_prompt + (DISCORD_FORMAT if discord else ''),
        effort="medium",
        mcp_servers={"fantasy": create_sdk_mcp_server(name="fantasy", tools=TOOLS)},
        tools=[],  # no built-in Claude Code tools (files, shell, web), only ours
        allowed_tools=[f"mcp__fantasy__{t.name}" for t in TOOLS],
        setting_sources=[],  # ignore local Claude Code settings and CLAUDE.md
        max_turns=30,
    )
    result = None
    async for message in query(prompt=f"Today is {date.today().isoformat()}. {question}",
                               options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result is None:
        return "No response from Claude."
    if result.is_error:
        return f"Claude failed: {result.result or result.subtype}"
    return result.result


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    args = sys.argv[1:]
    # Without --league, the agent works out the league from the question
    league = fantasy.pop_league_arg(args) if '--league' in args else None
    question = " ".join(args) or "Give me my daily briefing for each of my fantasy teams."
    try:
        print(asyncio.run(run(question, league)))
    except CLINotFoundError:
        print("Claude Code isn't installed. Install it, then run `claude` and /login.")
    except ClaudeSDKError as e:
        print(f"Claude Code failed: {e}\nIf you're not logged in, run `claude` and /login "
              "(or set CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`).")


if __name__ == '__main__':
    main()
