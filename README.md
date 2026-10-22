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

Keep `roster.json` up to date with your lineup (slot in `position`, e.g. `C`, `W`, `Util`, `D`, `G`, `BN`, `IR`).
Edit `league.scoring_categories` to match your league.

## Usage

```
python fantasy.py                      # schedule report, no AI, free
python agent.py                        # Claude daily briefing
python agent.py "Should I start Dostal on Saturday?"
```

## Files

- `nhl_api.py` - public NHL API client (schedule, player search, stats)
- `fantasy.py` - roster + schedule analysis: games per player, idle starters, bench swaps
- `agent.py` - Claude agent with tools over the above
- `test_yahoo.py` - Yahoo Fantasy API connection test (blocked pending Yahoo API approval)

## Yahoo

Since July 2026 Yahoo returns 403 for apps not approved through its developer access program,
so the roster is maintained by hand in `roster.json` for now. `oauth2.json` holds Yahoo
credentials and is git-ignored.
