# Fantasy Team Manager

An AI agent that monitors a Yahoo fantasy hockey team using public NHL data and Claude.

## Setup

```
pip install -r requirements.txt
```

Set an Anthropic API key (get one at https://console.anthropic.com):

```
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Each league has its own file in `leagues/`:

- `work.json` - Work League (the default)
- `pushing_pucks.json` - Pushing Pucks

Keep each file's roster up to date with your lineup (slot in `position`, e.g. `C`, `LW`, `W`, `Util`, `D`, `G`, `BN`, `IR`).
League settings live in `league`:

- `scoring` - keys are NHL API stat names, e.g. `goals`, `powerPlayPoints`, `shots`, `hits`, `blockedShots`, `saves`.
  Either one flat dict, or separate `skaters` and `goalies` dicts.
- `roster_positions` - the league's slots; if left out, they're inferred from where players sit now
- `min_goalie_starts_per_week`, `max_acquisitions_per_week`, `notes`

To add a league, copy one of these files.

## Usage

```
python fantasy.py                      # schedule report, no AI, free
python agent.py                        # Claude daily briefing
python agent.py "Should I start Dostal on Saturday?"
python fantasy.py drop Seth Jarvis      # who to drop to activate an IR player
python fantasy.py pickup waivers.txt    # rank free agents and who to drop for each
```

For `pickup`, copy a player list from Yahoo's Players page into a text file (or pipe it in).
Lines like `Jack Roslovic TOR - C,RW` work too.

Add `--league pushing_pucks` to any of these for the other league:

```
python fantasy.py --league pushing_pucks
python agent.py --league pushing_pucks "Who should I stream at D?"
```

## Files

- `leagues/*.json` - one file per league: settings, scoring and roster
- `nhl_api.py` - public NHL API client (schedule, player search, stats, hits/blocks)
- `scoring.py` - expected fantasy points per game from a league's scoring rules
- `fantasy.py` - roster + schedule analysis: best lineup for each day, goalie start tracking
- `agent.py` - Claude agent with tools over the above
- `test_yahoo.py` - Yahoo Fantasy API connection test (blocked pending Yahoo API approval)

## Yahoo

Since July 2026 Yahoo returns 403 for apps not approved through its developer access program,
so rosters are maintained by hand in `leagues/` for now. `oauth2.json` holds Yahoo
credentials and is git-ignored.
