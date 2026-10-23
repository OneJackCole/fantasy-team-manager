"""Expected fantasy points per game, from a league's scoring rules (leagues/<league>.json).

Scoring keys are NHL API stat names, e.g. {"goals": 1, "assists": 1, "wins": 1, "shutouts": 2}.
A league can score skaters and goalies separately:
{"skaters": {"goals": 6, ...}, "goalies": {"wins": 5, ...}}.
"""
import nhl_api

# How many games of last season's pace to blend into this season's numbers. Early in the
# season this keeps a hot or cold week from dominating; it fades as games accumulate.
PRIOR_GAMES = 20
FULL_SEASON = 82
MIN_PRIOR_GAMES = 20
# Goalie start share: team games of prior to blend in, and the prior when last season
# doesn't give one
START_PRIOR_GAMES = 8
TANDEM_START_RATE = 0.5


def rules_for(scoring, goalie):
    """The scoring rules that apply to a skater or a goalie."""
    if 'skaters' in scoring or 'goalies' in scoring:
        return scoring.get('goalies' if goalie else 'skaters', {})
    return scoring


def fantasy_points(stats, scoring):
    return sum(weight * stats.get(stat, 0) for stat, weight in scoring.items())


def _blend(current_pts, current_games, prior_rate):
    """Shrink this season's per-game rate toward last season's."""
    if prior_rate is None:
        return current_pts / current_games if current_games else 0.0
    return (current_pts + PRIOR_GAMES * prior_rate) / (current_games + PRIOR_GAMES)


def _seasons(player_id, scoring):
    """(this season's totals, most recent earlier season with a real sample).

    Skipping short seasons keeps an injury year from setting the baseline.
    """
    totals = nhl_api.get_season_totals(player_id)
    if any(stat in scoring for stat in nhl_api.REALTIME_STATS):
        for season, extra in nhl_api.get_realtime_totals(player_id).items():
            if season in totals:
                totals[season] = {**totals[season], **extra}
    season = nhl_api.current_season()
    cur = totals.get(season, {})
    earlier = [s for s in sorted(totals, reverse=True)
               if s != season and totals[s].get('gamesPlayed', 0) >= MIN_PRIOR_GAMES]
    prev = totals[earlier[0]] if earlier else {}
    return cur, prev


def skater_value(player_id, scoring):
    scoring = rules_for(scoring, goalie=False)
    cur, prev = _seasons(player_id, scoring)

    prior = (fantasy_points(prev, scoring) / prev['gamesPlayed']
             if prev.get('gamesPlayed') else None)
    cur_gp = cur.get('gamesPlayed', 0)
    value = _blend(fantasy_points(cur, scoring), cur_gp, prior)
    return {
        'value': round(value, 3),
        'basis': f"{cur_gp} GP this season"
                 + (f" + prior season's {prior:.2f}/GP" if prior is not None else ""),
    }


def goalie_value(player_id, team, scoring):
    """Expected points per *team* game: how often he starts x points per start."""
    scoring = rules_for(scoring, goalie=True)
    cur, prev = _seasons(player_id, scoring)
    team_gp = nhl_api.get_team_games_played().get(team, 0)

    cur_starts = cur.get('gamesStarted', 0)
    prev_starts = prev.get('gamesStarted', 0)
    prior_pts_per_start = (fantasy_points(prev, scoring) / prev_starts
                           if prev_starts else None)

    # A goalie's role changes faster than his quality, so the start share leans on this
    # season, borrows only from last season (an older one says little about the current
    # depth chart), and otherwise assumes a 50/50 tandem until starts accumulate.
    last_season = nhl_api.current_season() - 10001
    prior_start_rate = (min(1.0, prev_starts / FULL_SEASON)
                        if prev_starts and prev.get('season') == last_season
                        else TANDEM_START_RATE)
    start_rate = min(1.0, (cur_starts + START_PRIOR_GAMES * prior_start_rate)
                     / (team_gp + START_PRIOR_GAMES))
    pts_per_start = _blend(fantasy_points(cur, scoring), cur_starts, prior_pts_per_start)
    return {
        'value': round(start_rate * pts_per_start, 3),
        'start_rate': round(start_rate, 2),
        'points_per_start': round(pts_per_start, 2),
        'basis': f"{cur_starts} starts in {team_gp} team games this season",
    }


def goalie_start_dates(player_id, week_dates):
    """Dates this week the goalie has already started, per his game log."""
    return [g['gameDate'] for g in nhl_api.get_game_log(player_id)
            if g.get('gameDate') in week_dates and g.get('gamesStarted')]
