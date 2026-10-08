"""Wait for branch-based Pages runs before retrying an Actions deployment."""
import json
import os
import time
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
            competing.extend(run['id'] for run in runs
                             if run['id'] != current_run
                             and run['name'] == 'pages build and deployment')
        if not competing:
            print('No competing branch-based Pages runs; retrying deployment.')
            return
        if time.monotonic() >= deadline:
            raise TimeoutError('Competing Pages runs did not finish within 10 minutes.')
        print(f'Waiting for Pages runs: {competing}', flush=True)
        time.sleep(15)


if __name__ == '__main__':
    wait()
