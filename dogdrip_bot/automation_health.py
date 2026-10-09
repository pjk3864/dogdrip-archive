"""Track full daily collections and recover missing collection/deployment runs."""
import base64
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

STATUS_PATH = 'automation-status.json'
ACTIVE = {'queued', 'in_progress', 'pending', 'waiting', 'requested'}


def now_local():
    return datetime.datetime.now(ZoneInfo(os.environ.get('ARCHIVE_TIMEZONE', 'Asia/Bangkok')))


def api(method, path, payload=None):
    url = (os.environ.get('GITHUB_API_URL', 'https://api.github.com')
           + '/repos/' + os.environ['GITHUB_REPOSITORY'] + '/' + path)
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            transient = error.code in (429, 500, 502, 503, 504)
            if error.code == 403:
                transient = (error.headers.get('X-RateLimit-Remaining') == '0'
                             or 'secondary rate limit' in error.read().decode(errors='replace').lower())
            if not transient or attempt == 4:
                raise
            try:
                delay = min(60, max(2 ** (attempt + 1), float(error.headers.get('Retry-After', 0))))
            except ValueError:
                delay = 2 ** (attempt + 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 4:
                raise
            delay = 2 ** (attempt + 1)
        print(f'GitHub API temporary failure; retrying in {delay}s.', flush=True)
        time.sleep(delay)


def read_status():
    try:
        payload = api('GET', 'contents/' + STATUS_PATH)
        record = json.loads(base64.b64decode(payload['content']))
        if not isinstance(record, dict):
            raise ValueError('Invalid collection marker')
        return record
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        return {}
    except (ValueError, KeyError):
        print('::warning::Invalid collection marker; completion will be checked again.')
        return {}


def healthy_today(status, current):
    if status.get('timezone') != str(current.tzinfo) or status.get('local_date') != current.date().isoformat():
        return False
    completed = status.get('completed_at')
    if not completed:
        return False
    try:
        finished = datetime.datetime.fromisoformat(completed).astimezone(current.tzinfo)
    except (ValueError, TypeError):
        return False
    return finished.hour >= 10


def complete():
    current = now_local()
    record = {
        'local_date': current.date().isoformat(), 'timezone': str(current.tzinfo),
        'completed_at': current.astimezone(datetime.timezone.utc).isoformat(timespec='seconds'),
        'run_id': os.environ.get('GITHUB_RUN_ID', 'maintenance'),
        'archive_sha': api('GET', 'contents/archive.json')['sha'],
    }
    content = json.dumps(record, indent=2).encode()
    expected_sha = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    # Read the current SHA again on a lost response/conflict before retrying a PUT.
    for attempt in range(3):
        payload = {'message': 'Record verified full daily collection',
                   'content': base64.b64encode(content).decode(), 'branch': 'main'}
        try:
            existing = api('GET', 'contents/' + STATUS_PATH)
            if existing['sha'] == expected_sha:
                return
            payload['sha'] = existing['sha']
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        try:
            api('PUT', 'contents/' + STATUS_PATH, payload)
            print('Full collection recorded:', record['local_date'], record['timezone'])
            return
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            if attempt == 2 or (isinstance(error, urllib.error.HTTPError)
                                and error.code not in (409, 429, 500, 502, 503, 504)):
                raise
            time.sleep(2)


def gate():
    automatic = os.environ.get('GITHUB_EVENT_NAME') == 'schedule' or int(os.environ.get('RECOVERY_ATTEMPT', '0')) > 0
    should_run = not (automatic and healthy_today(read_status(), now_local()))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('run=' + str(should_run).lower() + '\n')
    print('Collect today.' if should_run else 'Today already completed; skip duplicate collection.')


def wait_until_ten():
    current = now_local()
    target = current.replace(hour=10, minute=0, second=0, microsecond=0)
    delay = max(0, (target - current).total_seconds())
    if delay > 20 * 60:
        raise RuntimeError('Unexpected schedule time; refusing an hours-long runner wait.')
    if delay:
        print(f'Preparing for 10:00 {current.tzinfo}; collection starts in {delay:.0f}s.', flush=True)
        time.sleep(delay)
    else:
        print(f'Starting collection now; target was 10:00 {current.tzinfo}.', flush=True)


def runs(workflow):
    return api('GET', f'actions/workflows/{workflow}/runs?per_page=100')['workflow_runs']


def recovery_number(run):
    match = re.search(r'recovery (\d+)', run.get('display_title', ''))
    return int(match.group(1)) if match else 0


def recover_workflow(workflow, history):
    if any(run['status'] in ACTIVE for run in history):
        print(workflow, 'already active; no duplicate dispatch.')
        return
    latest = history[0] if history else {}
    created = latest.get('created_at')
    failed_today = (created and datetime.datetime.fromisoformat(created.replace('Z', '+00:00'))
                    .astimezone(now_local().tzinfo).date() == now_local().date()
                    and latest.get('conclusion') in ('failure', 'timed_out', 'cancelled'))
    attempt = recovery_number(latest) + 1 if failed_today else 1
    if attempt > 2:
        raise RuntimeError(f'{workflow}: two recovery runs already failed; manual investigation required.')
    api('POST', f'actions/workflows/{workflow}/dispatches', {
        'ref': 'main', 'inputs': {'recovery_attempt': str(attempt)},
    })
    print(f'Dispatched {workflow}, recovery {attempt}.')


def public_manifest():
    url = 'https://pjk3864.github.io/dogdrip-archive/deployment.json?health=' + str(int(time.time()))
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            record = json.load(response)
            return record if isinstance(record, dict) else {}
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
        print('::warning::Could not confirm the public deployment; checking recovery.')
        return {}


def check():
    current = now_local()
    deployment_only = os.environ.get('RECOVERY_TRIGGER', '').startswith('Deploy Dogdrip Site')
    if not deployment_only and current.hour >= 10 and not healthy_today(read_status(), current):
        history = runs('daily-archive.yml')
        recover_workflow('daily-archive.yml', history)
        # Wait for the collection's workflow_run event to publish its new archive.
        return
    archive_sha = api('GET', 'contents/archive.json')['sha']
    if public_manifest().get('archive_sha') != archive_sha:
        recover_workflow('deploy-pages.yml', runs('deploy-pages.yml'))
    else:
        print('Public site matches the current archive. No recovery needed.')


def deployment_gate():
    should_deploy = True
    if os.environ.get('GITHUB_EVENT_NAME') == 'workflow_run':
        with open(os.environ['GITHUB_EVENT_PATH']) as source:
            run_id = json.load(source)['workflow_run']['id']
        jobs = api('GET', f'actions/runs/{run_id}/jobs?per_page=100')['jobs']
        should_deploy = not any(
            step['name'] == 'Archive new posts' and step.get('conclusion') == 'skipped'
            for job in jobs for step in job.get('steps', [])
        )
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('deploy=' + str(should_deploy).lower() + '\n')
    print('Deploy current archive.' if should_deploy else 'Collection skipped; no redundant deployment.')


if __name__ == '__main__':
    {'complete': complete, 'gate': gate, 'wait': wait_until_ten, 'check': check,
     'deployment-gate': deployment_gate}[sys.argv[1]]()
