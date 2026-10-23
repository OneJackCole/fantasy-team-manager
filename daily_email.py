"""Morning email: last night's results and today's best lineup, for every league.

Usage:
    python daily_email.py                 # send (needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD)
    python daily_email.py --preview       # write daily_email.html instead of sending
    python daily_email.py --briefing      # add a Claude briefing at the top (needs Claude Code)

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
import subprocess
import sys
from datetime import date, timedelta
from email.message import EmailMessage
from html import escape

import fantasy
import yahoo_api
from fantasy import slot_type

PREVIEW_FILE = 'daily_email.html'
SECRET_ENV = {'GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD', 'EMAIL_TO', 'YAHOO_CONSUMER_KEY',
              'YAHOO_CONSUMER_SECRET', 'YAHOO_REFRESH_TOKEN'}

STYLE = {
    'body': "font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1a1a1a;max-width:640px",
    'h2': "font-size:18px;margin:28px 0 4px;border-bottom:2px solid #1a1a1a;padding-bottom:4px",
    'h3': "font-size:14px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#555",
    'table': "border-collapse:collapse;width:100%;font-size:13px",
    'td': "padding:3px 8px 3px 0;border-bottom:1px solid #eee;vertical-align:top",
    'num': "padding:3px 0 3px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap",
    'muted': "color:#888",
    'alert': "background:#fff4e5;border-left:3px solid #e8890c;padding:6px 10px;margin:6px 0;font-size:13px",
    'briefing': "background:#f3f3f6;border-radius:6px;padding:10px 14px;margin:8px 0 4px;font-size:14px",
    'action': "background:#eaf4ea;border-left:3px solid #2e7d32;padding:6px 10px;margin:6px 0;font-size:13px",
}


def fmt(points):
    return f"{points:g}" if float(points).is_integer() else f"{points:.1f}"


def league_section(league, today):
    """(html, text, alerts, from_yahoo) for one league."""
    night = fantasy.last_night(today - timedelta(days=1), league)
    week = fantasy.week_analysis(today, league)
    settings = week['league']
    name = settings.get('name', league)
    from_yahoo = week['roster_source'] == 'yahoo'
    alerts = [week['roster_error']] if week['roster_error'] else []

    # Injured-slot players who played are probably ready to come back
    for pname in night['injured_played']:
        alerts.append(f"{pname} played last night from an IR slot - ready to activate?" if from_yahoo
                      else f"{pname} played last night but is on IR in the roster file - "
                           "activated? If so, update the file.")
    for p in week['players']:
        if p.get('team_mismatch'):
            alerts.append(f"{p['team_mismatch']} - probably a recent trade." if from_yahoo
                          else f"{p['team_mismatch']} - update the roster file.")

    today_plan = next((d for d in week['daily'] if d['date'] == today.isoformat()), None)
    gs = week['goalie_starts']
    goalie_line = (", ".join(f"{g['name']} {g['starts_so_far']} so far + ~{g['expected_remaining']}"
                             for g in gs['goalies'])
                   + f" -> ~{gs['projected_total']} projected"
                   + (f" (minimum {gs['minimum']})" if gs['minimum'] else ''))
    if gs['minimum'] and gs['projected_total'] < gs['minimum']:
        alerts.append(f"Goalie starts on pace for ~{gs['projected_total']}, "
                      f"below the minimum of {gs['minimum']}.")

    s = STYLE
    h = [f"<h2 style='{s['h2']}'>{escape(name)} <span style='{s['muted']};font-weight:normal'>"
         f"| {escape(week['team_name'])}</span></h2>"]
    t = [f"== {name} | {week['team_name']} =="]

    for a in alerts:
        h.append(f"<div style='{s['alert']}'>{escape(a)}</div>")
        t.append(f"! {a}")

    # Last night
    played = date.fromisoformat(night['date'])
    nd = f"{played:%a %b} {played.day}"
    h.append(f"<h3 style='{s['h3']}'>Last night ({nd})</h3>")
    t.append(f"\nLast night ({nd})")
    if night['best_lineup'] or night['others']:
        summary = f"Best possible lineup: {fmt(night['best_points'])} pts"
        if night['your_points'] is not None:
            summary = f"Your lineup: {fmt(night['your_points'])} pts  |  " + summary
        h.append(f"<p style='margin:0 0 6px;font-size:14px'><b>{escape(summary)}</b></p>"
                 f"<table style='{s['table']}'>")
        t.append(summary)
        rows = [(r, '') for r in night['best_lineup']] + [(r, s['muted']) for r in night['others']]
        for i, (p, muted) in enumerate(rows):
            if i == len(night['best_lineup']) and night['others']:
                h.append(f"<tr><td colspan='4' style='{s['td']};{s['muted']};padding-top:8px'>"
                         "Also played (no room, or on IR)</td></tr>")
                t.append("  Also played (no room, or on IR):")
            h.append(f"<tr style='{muted}'><td style='{s['td']}'>{escape(p['slot'])}</td>"
                     f"<td style='{s['td']}'>{escape(p['name'])}</td>"
                     f"<td style='{s['td']};color:#666'>{escape(p['line'])}</td>"
                     f"<td style='{s['num']}'><b>{fmt(p['points'])}</b></td></tr>")
            t.append(f"  {p['slot']:<5} {p['name']:<26} {fmt(p['points']):>6}  {p['line']}")
        h.append("</table>")
    else:
        h.append(f"<p style='{s['muted']};font-size:13px'>Nobody played.</p>")
        t.append("  Nobody played.")

    # Today. Only players with a game are listed: who sits in the other slots doesn't matter.
    h.append(f"<h3 style='{s['h3']}'>Today's best lineup</h3>")
    t.append("\nToday's best lineup")
    if today_plan is None or not today_plan['players_in_lineup_with_games']:
        h.append(f"<p style='{s['muted']};font-size:13px'>No games for your players today.</p>")
        t.append("  No games for your players today.")
    else:
        summary = (f"{today_plan['players_in_lineup_with_games']} with games, "
                   f"~{today_plan['projected_points']:.1f} projected pts")
        h.append(f"<p style='margin:0 0 6px;font-size:14px'><b>{escape(summary)}</b></p>")
        t.append(summary)
        if from_yahoo:
            # The lineup you've set is known, so say what to change
            moves = []
            if today_plan['start']:
                moves.append(f"Start {', '.join(today_plan['start'])}"
                             + (f"; bench {', '.join(today_plan['sit'])}" if today_plan['sit'] else ''))
            if today_plan['moved_slots']:
                moves.append(f"Move {', '.join(today_plan['moved_slots'])}")
            for m in moves or ["Your lineup is already the best one."]:
                h.append(f"<div style='{s['action'] if moves else s['muted']};font-size:13px'>"
                         f"{escape(m)}</div>")
                t.append(f"  {m}")
        value = {p['name']: p['value'] for p in week['players']}
        has_game = {p['name'] for p in week['players'] if today.isoformat() in p['game_dates']}
        opponent = {p['name']: next((('vs ' if g['home'] else '@ ') + g['opponent']
                                     for g in p['games'] if g['date'] == today.isoformat()), '')
                    for p in week['players']}
        h.append(f"<table style='{s['table']};margin-top:6px'>")
        for slot, pname in today_plan['optimal_lineup'].items():
            if pname not in has_game:
                continue
            h.append(f"<tr><td style='{s['td']}'>{slot_type(slot)}</td>"
                     f"<td style='{s['td']}'>{escape(pname)}</td>"
                     f"<td style='{s['td']};color:#666'>{escape(opponent[pname])}</td>"
                     f"<td style='{s['num']}'>{value[pname]:.2f}</td></tr>")
            t.append(f"  {slot_type(slot):<5} {pname:<26} {opponent[pname]:<8} {value[pname]:.2f}")
        h.append("</table>")
        if today_plan['playing_but_no_slot']:
            line = f"Playing but no room: {', '.join(today_plan['playing_but_no_slot'])}"
            h.append(f"<p style='{s['muted']};font-size:13px'>{escape(line)}</p>")
            t.append(f"  {line}")

    # Week
    h.append(f"<h3 style='{s['h3']}'>Goalie starts this week</h3>"
             f"<p style='font-size:13px;margin:0'>{escape(goalie_line)}</p>")
    t.append(f"\nGoalie starts: {goalie_line}")
    return '\n'.join(h), '\n'.join(t), alerts, from_yahoo


BRIEFING_PROMPT = """You write the top of a fantasy hockey manager's morning email. Below are \
each league's settings (scoring keys are NHL stat names) and this morning's report: last night's \
fantasy points and the best lineup in hindsight, today's projected best lineup, goalie starts \
against the weekly minimum, and alerts. Where a league shows "Your lineup: N pts" and today's \
Start/Bench/Move lines, the manager's real lineup is known: say what to change and how last night's \
lineup compared with the best possible. Where it doesn't, nobody knows which players were started, \
so never say a player was started, benched, or left on the bench.

Write a briefing of at most 6 short bullet lines across all leagues, most important first: lineup \
changes or who should be in today's lineup (especially close calls), alerts that need action, and \
anything notable from last night (standout games, a goalie-minimum risk). Name the league on each \
line. Don't repeat the full \
lineup and don't invent news, injuries or confirmed goalies the report doesn't contain. Plain text \
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
    context = f"League settings:\n{json.dumps(settings, indent=1)}\n\nThis morning's report:\n{text}"
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
    day = today.strftime('%a %b %d')
    subject = f"Fantasy hockey - {day}" + (f" ({alert_count} to check)" if alert_count else '')
    source_note = ("Rosters and lineups are read live from Yahoo." if from_yahoo else
                   "Rosters come from the league files in the repo - keep them current.")
    footer = (f"Projections are expected fantasy points per game from this season blended with "
              f"last. {source_note}")
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
