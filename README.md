# Fantasy Team Manager

An AI agent that monitors a Yahoo fantasy hockey team using public NHL data and Claude.

## Setup

```
pip install -r requirements.txt
```

`agent.py` runs Claude through Claude Code, so it uses your Claude subscription (Pro/Max), not
API billing. Install Claude Code and log in once (`claude`, then `/login`). On a machine without
a login, set `CLAUDE_CODE_OAUTH_TOKEN` to a token from `claude setup-token`. Leave
`ANTHROPIC_API_KEY` unset: if it's set, Claude Code bills that key instead.

Each league has its own file in `leagues/`:

- `work.json` - work league (the default)
- `pushing_pucks.json` - Pushing Pucks

`team_name` must match your team's name on Yahoo: with Yahoo set up (see below), the roster
and lineup are read live from it. Otherwise the file's roster is used - keep it up to date with
your lineup (slot in `position`, e.g. `C`, `LW`, `W`, `Util`, `D`, `G`, `BN`, `IR`).
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

## Morning email

`daily_email.py` emails last night's fantasy points and today's best lineup for every league,
plus alerts (an IR player who played, a traded player, goalie starts below the minimum).
`python daily_email.py --preview` writes `daily_email.html` without sending.

It runs every day at 8 AM Eastern on GitHub Actions (`.github/workflows/daily-email.yml`).
One-time setup:

1. **Gmail app password.** Google account -> Security -> turn on 2-Step Verification if it isn't,
   then go to https://myaccount.google.com/apppasswords, name it "fantasy email", and copy the
   16-character password.
2. **GitHub secrets.** In the repo on GitHub: Settings -> Secrets and variables -> Actions ->
   New repository secret, three times:
   - `GMAIL_ADDRESS` - the Gmail address
   - `GMAIL_APP_PASSWORD` - the app password (spaces are fine)
   - `EMAIL_TO` - where to send it (optional; defaults to `GMAIL_ADDRESS`, comma-separate several)
3. **Claude briefing (optional).** Run `claude setup-token` in a terminal and sign in with your
   Claude account, then add the token it prints as a fourth secret, `CLAUDE_CODE_OAUTH_TOKEN`.
   The briefing then runs on your Claude subscription (no API billing). Without the secret, or if
   Claude fails, the email goes out without it. `python daily_email.py --preview --briefing`
   tries it locally with your own Claude Code login.
4. **Yahoo (optional).** Add `YAHOO_CONSUMER_KEY`, `YAHOO_CONSUMER_SECRET` and
   `YAHOO_REFRESH_TOKEN` secrets (the values in your local `oauth2.json`, see below) for live
   rosters and lineups. Without them, the rosters in the league files are used.
5. **Test it.** Actions tab -> Daily fantasy email -> Run workflow.

## Discord bot

`discord_bot.py` lets you ask the agent from Discord with `/ask` (pick a league, or the default
work league). It only answers you, and replies are visible only to you. Claude runs on your
Claude subscription through Claude Code, as with `agent.py`. It runs on your PC, so it answers
while that's on and the bot is running.

1. **Create the bot.** https://discord.com/developers/applications -> New Application, name it.
   - Installation tab: set Install Link to **None** (needed before the bot can be private).
   - Bot tab: turn **Public Bot** off. Click **Reset Token** and copy the token (shown once).
2. **Invite it.** OAuth2 tab -> URL Generator: check `bot` and `applications.commands`, then
   under Bot Permissions check `Send Messages`. Open the generated URL and add the bot to a
   private server of your own (Discord: + -> Create My Own -> For me and my friends).
3. **Copy your IDs.** Discord -> User Settings -> Advanced -> turn on Developer Mode. Right-click
   your name -> Copy User ID; right-click the server icon -> Copy Server ID.
4. **Configure.** Create `discord_bot.json` in this folder (git-ignored):
   `{"token": "<bot token>", "user_id": "<your user ID>", "guild_id": "<server ID>"}`
5. **Run.** `pip install -r requirements.txt`, then `python discord_bot.py`. When it prints
   "Ready", type `/ask` in your server.

To start it automatically, add a Task Scheduler task (trigger: At log on) that runs
`pythonw.exe` with argument `discord_bot.py`, starting in this folder.

## Files

- `leagues/*.json` - one file per league: settings, scoring and a fallback roster
- `nhl_api.py` - public NHL API client (schedule, player search, stats, hits/blocks)
- `yahoo_api.py` - read-only Yahoo Fantasy client for your rosters and lineups
- `scoring.py` - expected fantasy points per game from a league's scoring rules
- `fantasy.py` - roster + schedule analysis: best lineup for each day, goalie appearances
- `agent.py` - Claude agent with tools over the above
- `daily_email.py` - morning email report, sent by the GitHub Actions workflow
- `discord_bot.py` - `/ask` the agent from Discord
- `test_yahoo.py` - one-time Yahoo sign-in and connection check

## Yahoo

1. Put your Yahoo app's credentials in `oauth2.json` (git-ignored):
   `{"consumer_key": "...", "consumer_secret": "..."}`
2. Run `python test_yahoo.py`. It opens Yahoo's sign-in, saves the tokens to `oauth2.json`,
   and checks each league file's `team_name` against your Yahoo teams.

Yahoo data is fetched live on each run and never written to disk or committed - don't commit
anything retrieved from Yahoo. Fantasy data provided by
[Yahoo Fantasy](https://sports.yahoo.com/fantasy/).
