"""Should this workflow run send the morning email? Prints send=true or send=false.

GitHub can delay or drop scheduled runs, so the workflow is scheduled several times a
morning. A scheduled run sends only if it's 8 AM Eastern or later and no earlier scheduled
run sent today's email. Manual runs always send.
"""
import json
import os
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')
SEND_FROM_HOUR = 8
WORKFLOW = 'daily-email.yml'
SEND_STEP = 'Send email'


def api(path):
    request = urllib.request.Request(f"https://api.github.com/{path}",
                                     headers={'Accept': 'application/vnd.github+json'})
    if os.environ.get('GITHUB_TOKEN'):
        request.add_header('Authorization', f"Bearer {os.environ['GITHUB_TOKEN']}")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def sent_today(now, repo, current_run):
    """Did a scheduled run already send today's email (Eastern time)?"""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    runs = api(f"repos/{repo}/actions/workflows/{WORKFLOW}/runs?event=schedule&per_page=20"
               f"&created=>={midnight:%Y-%m-%dT%H:%M:%SZ}")['workflow_runs']
    for run in runs:
        if str(run['id']) == str(current_run):
            continue
        for job in api(f"repos/{repo}/actions/runs/{run['id']}/jobs")['jobs']:
            if any(s['name'] == SEND_STEP and s['conclusion'] == 'success' for s in job['steps']):
                return True
    return False


def should_send(now=None):
    if os.environ.get('GITHUB_EVENT_NAME') != 'schedule':
        return True, "manual run"
    now = now or datetime.now(EASTERN)
    if now.hour < SEND_FROM_HOUR:
        return False, f"{now:%H:%M} Eastern is before {SEND_FROM_HOUR}:00"
    if sent_today(now, os.environ['GITHUB_REPOSITORY'], os.environ.get('GITHUB_RUN_ID')):
        return False, "today's email was already sent"
    return True, f"first run at or after {SEND_FROM_HOUR}:00 Eastern today ({now:%H:%M})"


if __name__ == '__main__':
    send, reason = should_send()
    print(f"{'Sending' if send else 'Not sending'}: {reason}")
    with open(os.environ.get('GITHUB_OUTPUT', os.devnull), 'a') as f:
        f.write(f"send={'true' if send else 'false'}\n")
