"""GitHub Actions OIDC admission. Runs unprivileged, never executes repository code."""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt

ISSUER = 'https://token.actions.githubusercontent.com'
AUDIENCE = 'https://wine.77-105-169-21.sslip.io/__deploy'
REPOSITORY = 'zoLikeCode/lct_2026_wine'
WORKFLOW = f'{REPOSITORY}/.github/workflows/deploy.yml@refs/heads/main'
SUBJECTS = {
    f'repo:{REPOSITORY}:ref:refs/heads/main',
    'repo:zoLikeCode@70718862/lct_2026_wine@1371476726:ref:refs/heads/main',
}
STATE = Path(os.environ.get('DEPLOY_STATE', '/var/lib/wine-deploy'))
JWKS = jwt.PyJWKClient(ISSUER + '/.well-known/jwks', timeout=5, cache_jwk_set=True)


def authenticate(token, key=None):
    signing_key = key or JWKS.get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, signing_key, algorithms=['RS256'], audience=AUDIENCE,
                        issuer=ISSUER, leeway=10, options={'require': [
                            'exp', 'iat', 'nbf', 'jti', 'sub', 'sha', 'run_id', 'run_attempt',
                            'repository_id', 'repository_owner_id', 'workflow_ref']})
    expected = {'repository': REPOSITORY, 'repository_id': '1371476726',
                'repository_owner_id': '70718862', 'ref': 'refs/heads/main',
                'workflow_ref': WORKFLOW, 'runner_environment': 'github-hosted'}
    if any(claims.get(k) != v for k, v in expected.items()):
        raise ValueError('Deployment identity does not match')
    if claims['sub'] not in SUBJECTS or claims.get('event_name') not in {'push', 'workflow_dispatch'}:
        raise ValueError('Only main push and main manual deployments are allowed')
    if claims['workflow_sha'] != claims['sha']:
        raise ValueError('Workflow must come from the deployed commit')
    if not re.fullmatch(r'[0-9a-f]{40}', claims['sha']):
        raise ValueError('Invalid commit')
    if not all(re.fullmatch(r'[0-9]{1,20}', str(claims[k])) for k in ('run_id', 'run_attempt')):
        raise ValueError('Invalid run')
    if claims['exp'] - claims['iat'] > 600 or time.time() - claims['iat'] > 600:
        raise ValueError('Token must be short-lived')
    return claims


def job_id(claims):
    return f"{claims['run_id']}.{claims['run_attempt']}"


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        # Do not log headers, tokens, or request bodies.
        print(f'{self.command} {self.path.split("?")[0]}', flush=True)

    def respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def identity(self):
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or len(auth) > 16384:
            raise ValueError('Missing token')
        return authenticate(auth[7:])

    def do_GET(self):
        if self.path == '/__deploy/version':
            p = STATE / 'current.json'
            return self.respond(200, json.loads(p.read_text()) if p.exists() else {'sha': None})
        try:
            claims = self.identity()
        except Exception:
            return self.respond(401, {'error': 'GitHub main deployment identity required'})
        ident = job_id(claims)
        if self.path != '/__deploy/jobs/' + ident:
            return self.respond(404, {'error': 'Unknown job'})
        p = STATE / 'status' / (ident + '.json')
        return self.respond(200, json.loads(p.read_text()) if p.exists() else {'status': 'queued'})

    def do_POST(self):
        if self.path != '/__deploy/jobs':
            return self.respond(404, {'error': 'Unknown route'})
        try:
            claims = self.identity()
        except Exception:
            return self.respond(401, {'error': 'GitHub main deployment identity required'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                return self.respond(413, {'error': 'Invalid body size'})
            payload = json.loads(self.rfile.read(length))
            if payload != {'sha': claims['sha']}:
                return self.respond(403, {'error': 'Commit must match the workflow token'})
            ident = job_id(claims)
            status = STATE / 'status' / (ident + '.json')
            if status.exists():
                return self.respond(200, {'job': ident, **json.loads(status.read_text())})
            queue = STATE / 'queue'
            if len(list(queue.glob('*.json'))) >= 20:
                return self.respond(429, {'error': 'Deployment queue is full'})
            p = queue / (ident + '.json')
            # Link an entirely written file into the queue atomically; retries cannot replace it.
            tmp = queue / ('.' + ident + '.' + os.urandom(8).hex())
            tmp.write_text(json.dumps({'sha': claims['sha'], 'job': ident, 'created': time.time()}))
            try:
                os.link(tmp, p)
            except FileExistsError:
                pass
            finally:
                tmp.unlink(missing_ok=True)
            return self.respond(202, {'job': ident, 'status': 'queued'})
        except (ValueError, TypeError, OSError):
            return self.respond(400, {'error': 'Invalid deployment request'})


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8120), Handler).serve_forever()
