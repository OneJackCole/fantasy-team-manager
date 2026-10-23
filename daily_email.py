"""Morning email: last night's results and today's best lineup, for every league.

Usage:
    python daily_email.py                 # send (needs GMAIL_ADDRESS and GMAIL_APP_PASSWORD)
    python daily_email.py --preview       # write daily_email.html instead of sending

Environment:
    GMAIL_ADDRESS       Gmail account that sends the email
    GMAIL_APP_PASSWORD  App password for that account (not the normal password)
    EMAIL_TO            Recipient(s), comma-separated; defaults to GMAIL_ADDRESS
"""
import os
import smtplib
import sys
from datetime import date, timedelta
from email.message import EmailMessage
from html import escape

import fantasy
from fantasy import INJURED_SLOTS, slot_type

PREVIEW_FILE = 'daily_email.html'

STYLE = {
    'body': "font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1a1a1a;max-width:640px",
    'h2': "font-size:18px;margin:28px 0 4px;border-bottom:2px solid #1a1a1a;padding-bottom:4px",
    'h3': "font-size:14px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#555",
    'table': "border-collapse:collapse;width:100%;font-size:13px",
    'td': "padding:3px 8px 3px 0;border-bottom:1px solid #eee;vertical-align:top",
    'num': "padding:3px 0 3px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap",
    'muted': "color:#888",
    'alert': "background:#fff4e5;border-left:3px solid #e8890c;padding:6px 10px;margin:6px 0;font-size:13px",
    'action': "background:#eaf4ea;border-left:3px solid #2e7d32;padding:6px 10px;margin:6px 0;font-size:13px",
}


def fmt(points):
    return f"{points:g}" if float(points).is_integer() else f"{points:.1f}"


def league_section(league, today):
    """(html, text, alerts) for one league."""
    night = fantasy.last_night(today - timedelta(days=1), league)
    week = fantasy.week_analysis(today, league)
    settings = week['league']
    name = settings.get('name', league)
    alerts = []

    # Injured-slot players who played are probably ready to come back
    for p in night['players']:
        if p['position'] in INJURED_SLOTS:
            alerts.append(f"{p['name']} played last night from {p['position']} - ready to activate?")
    for p in week['players']:
        if p.get('team_mismatch'):
            alerts.append(f"{p['team_mismatch']} - update the roster file.")

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
    if night['players']:
        summary = f"{fmt(night['active_points'])} pts from your lineup"
        if night['bench_points'] > 0:
            summary += f", {fmt(night['bench_points'])} left on the bench/IR"
        h.append(f"<p style='margin:0 0 6px;font-size:14px'><b>{escape(summary)}</b></p>"
                 f"<table style='{s['table']}'>")
        t.append(summary)
        for p in night['players']:
            muted = '' if p['active'] else s['muted']
            h.append(f"<tr style='{muted}'><td style='{s['td']}'>{escape(p['position'])}</td>"
                     f"<td style='{s['td']}'>{escape(p['name'])}</td>"
                     f"<td style='{s['td']};color:#666'>{escape(p['line'])}</td>"
                     f"<td style='{s['num']}'><b>{fmt(p['points'])}</b></td></tr>")
            t.append(f"  {p['position']:<5} {p['name']:<26} {fmt(p['points']):>6}  {p['line']}")
        h.append("</table>")
    else:
        h.append(f"<p style='{s['muted']};font-size:13px'>Nobody played.</p>")
        t.append("  Nobody played.")

    # Today
    h.append(f"<h3 style='{s['h3']}'>Today's lineup</h3>")
    t.append("\nToday's lineup")
    if today_plan is None or not today_plan['players_in_lineup_with_games']:
        h.append(f"<p style='{s['muted']};font-size:13px'>No games for your players today.</p>")
        t.append("  No games for your players today.")
    else:
        moves = []
        if today_plan['start']:
            moves.append(f"Start {', '.join(today_plan['start'])}; bench {', '.join(today_plan['sit'])}")
        if today_plan['moved_slots']:
            moves.append(f"Move {', '.join(today_plan['moved_slots'])}")
        summary = (f"{today_plan['players_in_lineup_with_games']} with games, "
                   f"~{today_plan['projected_points']:.1f} projected pts")
        if moves:
            summary += f" (current lineup: ~{today_plan['current_lineup_projected_points']:.1f})"
        h.append(f"<p style='margin:0 0 6px;font-size:14px'><b>{escape(summary)}</b></p>")
        t.append(summary)
        for m in moves or ["No changes needed."]:
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
            playing = pname in has_game
            note = f"{value[pname]:.2f}" if playing else ('no game' if pname else '')
            h.append(f"<tr style='{'' if playing else s['muted']}'>"
                     f"<td style='{s['td']}'>{slot_type(slot)}</td>"
                     f"<td style='{s['td']}'>{escape(pname or '-')}</td>"
                     f"<td style='{s['td']};color:#666'>{escape(opponent.get(pname, ''))}</td>"
                     f"<td style='{s['num']}'>{note}</td></tr>")
            t.append(f"  {slot_type(slot):<5} {pname or '-':<26} {opponent.get(pname, ''):<8} {note}")
        h.append("</table>")
        if today_plan['playing_but_no_slot']:
            line = f"Playing but no room: {', '.join(today_plan['playing_but_no_slot'])}"
            h.append(f"<p style='{s['muted']};font-size:13px'>{escape(line)}</p>")
            t.append(f"  {line}")

    # Week
    h.append(f"<h3 style='{s['h3']}'>Goalie starts this week</h3>"
             f"<p style='font-size:13px;margin:0'>{escape(goalie_line)}</p>")
    t.append(f"\nGoalie starts: {goalie_line}")
    return '\n'.join(h), '\n'.join(t), alerts


def build(today=None):
    """(subject, html, text) for all leagues."""
    today = today or date.today()
    html_parts, text_parts, alert_count = [], [], 0
    for league in fantasy.available_leagues():
        if not fantasy.all_players(fantasy.load_roster(league)):
            continue
        try:
            h, t, alerts = league_section(league, today)
        except Exception as e:  # one league failing shouldn't sink the whole email
            h = f"<h2 style='{STYLE['h2']}'>{escape(league)}</h2><div style='{STYLE['alert']}'>" \
                f"Couldn't build this section: {escape(repr(e))}</div>"
            t, alerts = f"== {league} ==\nCouldn't build this section: {e!r}", [e]
        html_parts.append(h)
        text_parts.append(t)
        alert_count += len(alerts)
    day = today.strftime('%a %b %d')
    subject = f"Fantasy hockey - {day}" + (f" ({alert_count} to check)" if alert_count else '')
    html = (f"<html><body style='{STYLE['body']}'>" + '\n'.join(html_parts)
            + f"<p style='{STYLE['muted']};font-size:11px;margin-top:28px'>Projections are expected "
              "fantasy points per game from this season blended with last. Rosters come from the "
              "league files in the repo - keep them current.</p></body></html>")
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
    print(f"Sent '{subject}' to {', '.join(recipients)}")


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    subject, html, text = build()
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
