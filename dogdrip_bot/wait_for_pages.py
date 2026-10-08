"""Stop competing branch-based runs so the small Actions site deploys last."""
import json
import datetime
import os
import time
import urllib.error
import urllib.request


def wait():
    repository = os.environ['GITHUB_REPOSITORY']
    current_run = int(os.environ['GITHUB_RUN_ID'])
    headers = {
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    deadline = time.monotonic() + 600
    cancelled = set()
    # A conflicting deployment may take a moment to appear in the runs API.
    time.sleep(30)
    while True:
        competing = []
        for status in ('queued', 'in_progress', 'waiting', 'pending'):
            url = (os.environ.get('GITHUB_API_URL', 'https://api.github.com')
                   + f'/repos/{repository}/actions/runs?status={status}&per_page=100')
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                runs = json.load(response)['workflow_runs']
            for run in runs:
                if run['id'] == current_run or run['name'] != 'pages build and deployment':
                    continue
                created = run.get('created_at')
                # Dynamic Pages can retain orphaned, jobless queue entries for days.
                # They are not active deployments; still stop every in-progress run.
                if status != 'in_progress' and created:
                    started = datetime.datetime.fromisoformat(created.replace('Z', '+00:00'))
                    if datetime.datetime.now(datetime.timezone.utc) - started > datetime.timedelta(hours=6):
                        print(f'Skipping stale Pages queue entry {run["id"]}.', flush=True)
                        continue
                competing.append(run['id'])
        if not competing:
            print('No competing branch-based Pages runs; retrying deployment.')
            return
        for run_id in competing:
            if run_id not in cancelled:
                url = (os.environ.get('GITHUB_API_URL', 'https://api.github.com')
                       + f'/repos/{repository}/actions/runs/{run_id}/cancel')
                request = urllib.request.Request(url, headers=headers, method='POST')
                try:
                    with urllib.request.urlopen(request, timeout=30):
                        pass
                except urllib.error.HTTPError as error:
                    # It may have completed between listing and cancellation.
                    if error.code != 409:
                        raise
                cancelled.add(run_id)
                print(f'Stopped competing branch-based Pages run {run_id}.', flush=True)
        if time.monotonic() >= deadline:
            raise TimeoutError('Competing Pages runs did not finish within 10 minutes.')
        print(f'Waiting for Pages runs: {competing}', flush=True)
        time.sleep(15)


if __name__ == '__main__':
    wait()
