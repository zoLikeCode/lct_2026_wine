"""Run only in a GitHub Actions job with id-token:write. No long-lived secret."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://wine.77-105-169-21.sslip.io/__deploy'


def request(path, body=None):
    token_url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL'] + '&audience=' + urllib.parse.quote(BASE, safe='')
    req = urllib.request.Request(token_url, headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with urllib.request.urlopen(req, timeout=20) as r:
        token = json.load(r)['value']
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    sha = os.environ['GITHUB_SHA']
    queued = request('/jobs', {'sha': sha})
    print('Deployment queued:', queued['job'], sha, flush=True)
    until = time.monotonic() + 900
    previous = None
    while time.monotonic() < until:
        status = request('/jobs/' + queued['job'])
        if status['status'] != previous:
            print(json.dumps(status), flush=True)
            previous = status['status']
        if status['status'] == 'success':
            if status['sha'] != sha:
                raise RuntimeError('Server deployed a different commit')
            return
        if status['status'] == 'superseded':
            print('A newer main commit is already queued; skipping this stale release.')
            return
        if status['status'] == 'failed':
            raise RuntimeError(status.get('error', 'Deployment failed'))
        time.sleep(10)
    raise TimeoutError('Deployment did not finish within 15 minutes')


if __name__ == '__main__':
    main()
