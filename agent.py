"""Fantasy hockey agent: Claude + NHL data tools.

Usage:
    python agent.py                      # daily briefing
    python agent.py "Should I start Forsberg tonight?"
"""
import json
import sys
from datetime import date

import anthropic
from anthropic import beta_tool

import fantasy
import nhl_api

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are a fantasy hockey assistant managing a Yahoo fantasy hockey team \
for the user. You give concise, practical advice: who to start, who to sit, and where the \
schedule creates an edge. The league's format, scoring rules, and goalie-start minimum are in \
the `league` field of get_week_overview.

How to work:
- Start with get_week_overview. Each player has a `value`: expected fantasy points per game
  (goalies: per team game, factoring in how often they start), blending this season with a
  prior healthy season. Each remaining day has the lineup that maximizes total value, and there
  is a goalie-starts projection against the weekly minimum. Treat those lineups as the baseline;
  override only with a concrete reason (hot or cold streak, role change, likely goalie rest day).
- Pull get_player_stats for players whose start/sit call is close, or whose recent form matters.
- Use get_team_game_counts when suggesting pickups or streamers by schedule.
- When a player comes off IR, use evaluate_drop_candidates to decide who to drop.
- Players in BN can be swapped into an active slot only if their eligible_positions fit that slot \
(Util takes any skater). Players in IR/IR+ slots are injured and can't play.

Limits you must respect:
- You do NOT know which players are free agents in the user's league. When you suggest a pickup, \
frame it as "if available" and suggest checking the waiver wire.
- You have no injury news or confirmed starting goalies beyond what the stats show. Say so when \
it matters (especially for goalie starts).
- If a player's NHL team in the stats differs from the roster file, point it out - they were \
probably traded and roster.json needs updating. (Yahoo and NHL abbreviations like NJ/NJD and \
TB/TBL are the same team - don't flag those.)

Format: plain text for a terminal. Lead with the actionable moves for today, then the rest of \
the week, then anything worth watching. Keep it short."""


@beta_tool
def get_week_overview() -> str:
    """Get the user's fantasy roster with this week's NHL schedule.

    Returns each player's lineup slot, eligible positions, NHL team, games this week and
    games remaining, and expected fantasy points per game (`value`). For each remaining day:
    the lineup with the highest expected points, which bench players to start, which starters
    to bench, slot moves, and projected points vs. the current lineup. Also projects goalie
    starts for the week against the league minimum.
    """
    return json.dumps(fantasy.week_analysis())


@beta_tool
def get_player_stats(name: str, team: str = "") -> str:
    """Get current-season stats and the last 5 games for an NHL player.

    Args:
        name: Player's full name, e.g. "Filip Forsberg".
        team: Optional NHL team abbreviation to disambiguate players with the same name.
    """
    player = nhl_api.find_player(name, team or None)
    if player is None:
        return json.dumps({'error': f"No active NHL player found named {name!r}"})
    return json.dumps(nhl_api.get_player_stats(player['id']))


@beta_tool
def evaluate_drop_candidates(activate_player: str) -> str:
    """Rank who to drop to make room for a player coming off IR.

    For each droppable player, projects the team's total fantasy points over the next 4 weeks
    of the real NHL schedule (best lineup every day) and expected goalie starts per week.
    Options that would fall below the weekly goalie-start minimum are ranked last.

    Args:
        activate_player: Name of the rostered IR player being activated, e.g. "Seth Jarvis".
    """
    try:
        return json.dumps(fantasy.drop_analysis(activate_player))
    except ValueError as e:
        return json.dumps({'error': str(e)})


@beta_tool
def get_team_game_counts() -> str:
    """Get how many games every NHL team plays this fantasy week (Mon-Sun) and how many
    remain from today, sorted by games remaining. Useful for finding streaming targets."""
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


def run(question):
    client = anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "medium"},
        # If Claude declines a request, retry it automatically on a fallback model
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        tools=[get_week_overview, get_player_stats, evaluate_drop_candidates,
               get_team_game_counts],
        messages=[{"role": "user", "content": f"Today is {date.today().isoformat()}. {question}"}],
    )

    final = None
    for message in runner:
        final = message
    if final is None:
        return "No response from Claude."
    if final.stop_reason == "refusal":
        return "Claude declined to answer this request."
    return "\n".join(block.text for block in final.content if block.type == "text")


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    question = " ".join(sys.argv[1:]) or "Give me my daily briefing for my fantasy team."
    try:
        print(run(question))
    except anthropic.AuthenticationError:
        print("Anthropic API key missing or invalid. Set the ANTHROPIC_API_KEY environment variable.")
    except anthropic.APIConnectionError:
        print("Couldn't reach the Anthropic API. Check your internet connection.")


if __name__ == '__main__':
    main()
