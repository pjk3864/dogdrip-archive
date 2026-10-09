"""Stop competing branch-based runs so the small Actions site deploys last."""
import datetime
import os
import time
import urllib.error
from automation_health import api


def wait():
    current_run = int(os.environ['GITHUB_RUN_ID'])
    deadline = time.monotonic() + 600
    cancelled = {}
    forced = set()
    # A conflicting deployment may take a moment to appear in the runs API.
    time.sleep(30)
    while True:
        competing = []
        in_progress = set()
        for status in ('queued', 'in_progress', 'waiting', 'pending'):
            runs = api('GET', f'actions/runs?status={status}&per_page=100')['workflow_runs']
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
                if status == 'in_progress':
                    in_progress.add(run['id'])
        if not competing:
            print('No competing branch-based Pages runs; retrying deployment.')
            return
        for run_id in competing:
            if run_id not in cancelled:
                try:
                    api('POST', f'actions/runs/{run_id}/cancel')
                except urllib.error.HTTPError as error:
                    # It may have completed between listing and cancellation.
                    if error.code != 409:
                        raise
                cancelled[run_id] = time.monotonic()
                print(f'Stopped competing branch-based Pages run {run_id}.', flush=True)
            elif (run_id in in_progress and run_id not in forced
                  and time.monotonic() - cancelled[run_id] >= 90):
                try:
                    api('POST', f'actions/runs/{run_id}/force-cancel')
                except urllib.error.HTTPError as error:
                    if error.code != 409:
                        raise
                forced.add(run_id)
                print(f'Forced completion of stalled legacy run {run_id}.', flush=True)
        if time.monotonic() >= deadline:
            raise TimeoutError('Competing Pages runs did not finish within 10 minutes.')
        print(f'Waiting for Pages runs: {competing}', flush=True)
        time.sleep(15)


if __name__ == '__main__':
    wait()
