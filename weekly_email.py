"""Monday email: the week ahead for every league - each day's best lineup, the goalie minimum,
and roster spots worth a look on the waiver wire.

Usage:
    python weekly_email.py                 # send (needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD)
    python weekly_email.py --preview       # write weekly_email.html instead of sending
    python weekly_email.py --briefing      # add a Claude briefing at the top (needs Claude Code)

Environment:
    GMAIL_ADDRESS       Gmail account that sends the email
    GMAIL_APP_PASSWORD  App password for that account (not the normal password)
    EMAIL_TO            Recipient(s), comma-separated; defaults to GMAIL_ADDRESS
    CLAUDE_CODE_OAUTH_TOKEN  For --briefing on a machine where Claude Code isn't logged in
                        (from `claude setup-token`; runs on the Claude subscription, not the API)
"""
import json
import os
import shutil
import smtplib
import statistics
import subprocess
import sys
from datetime import date
from email.message import EmailMessage
from html import escape

import fantasy
import nhl_api
import scoring
import yahoo_api
from fantasy import INJURED_SLOTS, slot_type

PREVIEW_FILE = 'weekly_email.html'
SECRET_ENV = {'GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD', 'EMAIL_TO', 'YAHOO_CONSUMER_KEY',
              'YAHOO_CONSUMER_SECRET', 'YAHOO_REFRESH_TOKEN'}

# "Worth a look" thresholds for skaters
LOW_VALUE_SHARE = 0.75   # projection under this share of the roster's median skater
COLD_SHARE = 0.5         # last 5 games averaging under this share of his projection
RECENT_GAMES = 5

STYLE = {
    'body': "font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1a1a1a;max-width:640px",
    'h2': "font-size:18px;margin:28px 0 4px;border-bottom:2px solid #1a1a1a;padding-bottom:4px",
    'h3': "font-size:14px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#555",
    'table': "border-collapse:collapse;width:100%;font-size:13px",
    'td': "padding:3px 8px 3px 0;border-bottom:1px solid #eee;vertical-align:top",
    'cell': "padding:3px 2px;border-bottom:1px solid #eee;text-align:center;white-space:nowrap",
    'num': "padding:3px 0 3px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap",
    'muted': "color:#888",
    'alert': "background:#fff4e5;border-left:3px solid #e8890c;padding:6px 10px;margin:6px 0;font-size:13px",
    'briefing': "background:#f3f3f6;border-radius:6px;padding:10px 14px;margin:8px 0 4px;font-size:14px",
    'ok': "background:#eaf4ea;border-left:3px solid #2e7d32;padding:6px 10px;margin:6px 0;font-size:13px",
}


def fmt(points):
    return f"{points:g}" if float(points).is_integer() else f"{points:.1f}"


def actual_averages(player, rules):
    """A skater's fantasy points per game (this season, last few games); None where too few."""
    if not player.get('nhl_id'):
        return None, None
    points = [scoring.fantasy_points(g, rules) for g in nhl_api.get_skater_games(player['nhl_id'])]
    season = statistics.mean(points) if points else None
    recent = statistics.mean(points[-RECENT_GAMES:]) if len(points) >= RECENT_GAMES else None
    return season, recent


def worth_a_look(players, starts, rules):
    """Active skaters with a weak week ahead: [(name, reasons)]."""
    skaters = [p for p in players
               if p['position'] not in INJURED_SLOTS and 'G' not in p['eligible_positions']]
    if not skaters:
        return []
    median = statistics.median(p['value'] for p in skaters)
    flagged = []
    for p in skaters:
        reasons = []
        n = starts[p['name']]
        if n <= 1:
            reasons.append(f"{'only 1 game' if n == 1 else 'no games'} in the best lineup this week"
                           + (f" ({p['games_this_week']} on the schedule)"
                              if p['games_this_week'] > n else ''))
        season, recent = actual_averages(p, rules)
        if p['value'] < LOW_VALUE_SHARE * median:
            # The projection leans on last season too, so show what he's doing this season
            reasons.append(f"projects {p['value']:.2f}/game, well below your median skater "
                           f"({median:.2f})" + (f" - {season:.2f}/game this season"
                                                if season is not None else ''))
        if recent is not None and recent < COLD_SHARE * p['value']:
            reasons.append(f"cold: {recent:.1f}/game over his last {RECENT_GAMES}, "
                           f"vs {p['value']:.2f} projected")
        if reasons:
            flagged.append((p['name'], reasons))
    return flagged


def league_section(league, today):
    """(html, text, alerts, from_yahoo) for one league."""
    week = fantasy.week_analysis(today, league)
    settings = week['league']
    name = settings.get('name', league)
    from_yahoo = week['roster_source'] == 'yahoo'
    alerts = [week['roster_error']] if week['roster_error'] else []
    players = week['players']
    for p in players:
        if p.get('team_mismatch'):
            alerts.append(f"{p['team_mismatch']} - probably a recent trade." if from_yahoo
                          else f"{p['team_mismatch']} - update the roster file.")

    days = week['daily']
    active = [p for p in players if p['position'] not in INJURED_SLOTS]
    # Who's in each day's best lineup with a game, and in which slot
    slot_on = {d['date']: {pname: slot_type(slot) for slot, pname in d['optimal_lineup'].items()
                           if pname} for d in days}
    starts = {p['name']: sum(1 for d in days if d['date'] in p['game_dates']
                             and p['name'] in slot_on[d['date']])
              for p in active}

    s = STYLE
    h = [f"<h2 style='{s['h2']}'>{escape(name)} <span style='{s['muted']};font-weight:normal'>"
         f"| {escape(week['team_name'])}</span></h2>"]
    t = [f"== {name} | {week['team_name']} =="]

    # Goalies: the verdict, plus the games behind it
    gs = week['goalie_starts']
    goalies = [g for g in gs['goalies']
               if next(p for p in players if p['name'] == g['name'])['position'] not in INJURED_SLOTS]
    minimum = gs['minimum']
    if minimum:
        on_track = gs['projected_total'] >= minimum
        verdict = (f"On track for the {minimum}-appearance minimum." if on_track else
                   f"Likely to miss the {minimum}-appearance minimum - consider streaming a goalie.")
        if not on_track:
            alerts.append(f"Goalies likely to miss the minimum of {minimum} this week.")
    else:
        verdict = "No goalie minimum."
    goalie_games = []
    for g in goalies:
        p = next(p for p in players if p['name'] == g['name'])
        goalie_games.append(f"{g['name']}: {p['games_this_week']} team games, "
                            f"starts ~{round(p.get('start_rate', 0) * 100)}% of them")

    for a in alerts:
        h.append(f"<div style='{s['alert']}'>{escape(a)}</div>")
        t.append(f"! {a}")

    h.append(f"<h3 style='{s['h3']}'>Goalies</h3>"
             f"<div style='{s['ok'] if not minimum or gs['projected_total'] >= minimum else s['alert']}'>"
             f"{escape(verdict)}</div>"
             + ''.join(f"<div style='font-size:13px;margin:2px 0'>{escape(line)}</div>"
                       for line in goalie_games))
    t.append(f"\nGoalies: {verdict}")
    t.extend(f"  {line}" for line in goalie_games)

    # Lineups: one row per player, one column per day
    h.append(f"<h3 style='{s['h3']}'>Best lineup each day</h3>")
    t.append("\nBest lineup each day (slot = start, bn = plays but no room, . = no game)")
    if not days:
        h.append(f"<p style='{s['muted']};font-size:13px'>No days left this week.</p>")
    else:
        labels = [date.fromisoformat(d['date']).strftime('%a') for d in days]
        h.append(f"<table style='{s['table']}'><tr><td style='{s['td']}'></td>"
                 + ''.join(f"<td style='{s['cell']};{s['muted']}'>{lab}</td>" for lab in labels)
                 + f"<td style='{s['num']};{s['muted']}'>Starts</td></tr>")
        t.append(f"  {'':<24}" + ''.join(f"{lab:>5}" for lab in labels) + "  starts")
        order = {'C': 0, 'LW': 1, 'RW': 2, 'W': 1, 'D': 3, 'G': 4}
        for p in sorted(active, key=lambda p: (order.get(p['eligible_positions'][0], 5),
                                               -p['value'])):
            cells = []
            for d in days:
                if d['date'] not in p['game_dates']:
                    cells.append('.')
                else:
                    cells.append(slot_on[d['date']].get(p['name'], 'bn'))
            h.append(f"<tr><td style='{s['td']}'>{escape(p['name'])}</td>"
                     + ''.join(f"<td style='{s['cell']}"
                               + (f";{s['muted']}" if c in ('.', 'bn') else ';font-weight:600')
                               + f"'>{'' if c == '.' else c}</td>" for c in cells)
                     + f"<td style='{s['num']}'>{starts[p['name']]}</td></tr>")
            t.append(f"  {p['name'][:24]:<24}" + ''.join(f"{c:>5}" for c in cells)
                     + f"  {starts[p['name']]:>5}")
        totals = [d['projected_points'] for d in days]
        h.append(f"<tr><td style='{s['td']};{s['muted']}'>Projected pts</td>"
                 + ''.join(f"<td style='{s['cell']};{s['muted']}'>{x:.0f}</td>" for x in totals)
                 + f"<td style='{s['num']}'><b>{sum(totals):.0f}</b></td></tr></table>")
        t.append(f"  {'Projected pts':<24}" + ''.join(f"{x:>5.0f}" for x in totals)
                 + f"  {sum(totals):>5.0f}")

    # Weak spots
    rules = scoring.rules_for(settings.get('scoring', {}), goalie=False)
    flagged = worth_a_look(players, starts, rules)
    h.append(f"<h3 style='{s['h3']}'>Worth a look on waivers</h3>")
    t.append("\nWorth a look on waivers")
    if flagged:
        for pname, reasons in flagged:
            h.append(f"<div style='font-size:13px;margin:3px 0'><b>{escape(pname)}</b> - "
                     f"{escape('; '.join(reasons))}</div>")
            t.append(f"  {pname}: {'; '.join(reasons)}")
    else:
        h.append(f"<p style='{s['muted']};font-size:13px'>Nobody stands out this week.</p>")
        t.append("  Nobody stands out this week.")
    return '\n'.join(h), '\n'.join(t), alerts, from_yahoo


BRIEFING_PROMPT = """You write the top of a fantasy hockey manager's Monday email about the week \
ahead. Below are each league's settings (scoring keys are NHL stat names) and the report: the \
goalie verdict against the weekly appearance minimum, a grid of each day's projected best lineup \
(a slot means start, "bn" means he plays but there's no room, "." means no game), and players \
flagged as worth a look on waivers.

Write a briefing of at most 6 short bullet lines across all leagues, most important first: a \
goalie minimum at risk, the flagged players and whether they look like real problems or just a \
light schedule, and notable lineup decisions (players who often sit, or close calls between \
players for one slot). Name the league on each line. Talk about goalie appearances as whole \
starts and only say whether the minimum is on track or likely to be missed. Don't repeat the \
grid and don't invent news, injuries or confirmed goalies the report doesn't contain. Plain text \
only: each line starts with "- ", no markdown, no heading, no sign-off. Answer directly without \
using any tools."""


def claude_briefing(text, leagues):
    """A short briefing from Claude Code (`claude -p`), or None if it isn't available or fails.

    Uses Claude Code's own login (or CLAUDE_CODE_OAUTH_TOKEN), so it runs on the Claude
    subscription rather than API billing.
    """
    claude = shutil.which('claude')
    if claude is None:
        print("Briefing skipped: Claude Code (`claude`) is not installed", file=sys.stderr)
        return None
    settings = {league: fantasy.load_roster(league).get('league', {}) for league in leagues}
    context = f"League settings:\n{json.dumps(settings, indent=1)}\n\nThis week's report:\n{text}"
    try:
        # Claude reads outside text, so keep the mail and Yahoo credentials out of its environment
        env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
        result = subprocess.run([claude, '-p', BRIEFING_PROMPT], input=context, capture_output=True,
                                text=True, encoding='utf-8', timeout=300, env=env)
    except subprocess.TimeoutExpired:
        print("Briefing skipped: Claude Code timed out", file=sys.stderr)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        # Only stderr: stdout would be the briefing, which holds roster data and the
        # Actions log of a public repo is public
        print(f"Briefing skipped: Claude Code exited {result.returncode}: "
              f"{result.stderr.strip()[:300]}", file=sys.stderr)
        return None
    return result.stdout.strip()


def build(today=None, briefing=False):
    """(subject, html, text) for all leagues."""
    today = today or date.today()
    html_parts, text_parts, alert_count, leagues, from_yahoo = [], [], 0, [], False
    for league in fantasy.available_leagues():
        roster = fantasy.load_roster(league)
        if not fantasy.all_players(roster):
            if roster.get('source_error'):  # no file roster to fall back on - say why
                problem = roster['source_error']
                html_parts.append(f"<h2 style='{STYLE['h2']}'>{escape(league)}</h2>"
                                  f"<div style='{STYLE['alert']}'>{escape(problem)}</div>")
                text_parts.append(f"== {league} ==\n! {problem}")
                alert_count += 1
            continue
        leagues.append(league)
        try:
            h, t, alerts, yahoo = league_section(league, today)
            from_yahoo = from_yahoo or yahoo
        except Exception as e:  # one league failing shouldn't sink the whole email
            h = f"<h2 style='{STYLE['h2']}'>{escape(league)}</h2><div style='{STYLE['alert']}'>" \
                f"Couldn't build this section: {escape(repr(e))}</div>"
            t, alerts = f"== {league} ==\nCouldn't build this section: {e!r}", [e]
        html_parts.append(h)
        text_parts.append(t)
        alert_count += len(alerts)
    summary = claude_briefing('\n\n'.join(text_parts), leagues) if briefing else None
    if summary:
        lines = [line.strip().lstrip('-* ').strip() for line in summary.splitlines() if line.strip()]
        html_parts.insert(0, f"<div style='{STYLE['briefing']}'><b>Briefing</b><ul style='margin:6px 0 0;"
                             "padding-left:18px'>"
                          + ''.join(f"<li style='margin:3px 0'>{escape(line)}</li>" for line in lines)
                          + "</ul></div>")
        text_parts.insert(0, "Briefing\n" + '\n'.join(f"- {line}" for line in lines))
    subject = f"Fantasy hockey - week of {today:%b} {today.day}" + (
        f" ({alert_count} to check)" if alert_count else '')
    source_note = ("Rosters are read live from Yahoo." if from_yahoo else
                   "Rosters come from the league files in the repo - keep them current.")
    footer = (f"Projections are expected fantasy points per game from this season blended with "
              f"last. Lineups assume the roster stays as it is all week. {source_note}")
    attribution = ''
    if from_yahoo:  # required wherever Yahoo data is shown
        attribution = (f"<br><a href='{yahoo_api.ATTRIBUTION_URL}' style='color:#888'>"
                       f"{yahoo_api.ATTRIBUTION}</a>")
        text_parts.append(f"{yahoo_api.ATTRIBUTION} ({yahoo_api.ATTRIBUTION_URL})")
    html = (f"<html><body style='{STYLE['body']}'>" + '\n'.join(html_parts)
            + f"<p style='{STYLE['muted']};font-size:11px;margin-top:28px'>{escape(footer)}"
              f"{attribution}</p></body></html>")
    return subject, html, '\n\n'.join(text_parts)


def send(subject, html, text):
    sender = os.environ['GMAIL_ADDRESS']
    recipients = [r.strip() for r in (os.environ.get('EMAIL_TO') or sender).split(',') if r.strip()]
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)
    msg.set_content(text)
    msg.add_alternative(html, subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(sender, os.environ['GMAIL_APP_PASSWORD'])
        smtp.send_message(msg)
    print(f"Sent '{subject}' to {len(recipients)} recipient(s)")


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    subject, html, text = build(briefing='--briefing' in sys.argv)
    if not os.environ.get('GMAIL_APP_PASSWORD') and os.environ.get('CI'):
        sys.exit("GMAIL_ADDRESS / GMAIL_APP_PASSWORD secrets are not set")
    if '--preview' in sys.argv or not os.environ.get('GMAIL_APP_PASSWORD'):
        with open(PREVIEW_FILE, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"{subject}\n\n{text}\n\nWrote {PREVIEW_FILE} (not sent)")
        return
    send(subject, html, text)


if __name__ == '__main__':
    main()
