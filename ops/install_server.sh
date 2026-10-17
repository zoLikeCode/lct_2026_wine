#!/usr/bin/env bash
# Bootstrap once over an already trusted administrator SSH connection.
set -euo pipefail
cd "$(dirname "$0")"
test "$(id -u)" = 0
getent passwd wine-deploy >/dev/null || useradd --system --home /var/lib/wine-deploy --shell /usr/sbin/nologin wine-deploy
install -d -m 755 /usr/local/lib/wine-deploy /var/lib/wine-deploy /var/lib/wine-deploy/status
install -d -m 770 -o root -g wine-deploy /var/lib/wine-deploy/queue
install -m 644 oidc_gateway.py deploy_release.py requirements.txt /usr/local/lib/wine-deploy/
if [ ! -x /opt/wine/deploy-venv/bin/python ]; then
  python3 -m venv /opt/wine/deploy-venv
fi
/opt/wine/deploy-venv/bin/python -m pip install -r /usr/local/lib/wine-deploy/requirements.txt
# The approved application dependency fingerprint is passed separately; never hash ops requirements here.
install -m 644 runtime-requirements.sha256 /var/lib/wine-deploy/runtime-requirements.sha256
if [ ! -L /opt/wine/current ]; then
  test ! -e /opt/wine/current
  ln -s /opt/wine /opt/wine/current
fi
install -d -m 755 /etc/systemd/system/wine.service.d
cat > /etc/systemd/system/wine.service.d/releases.conf <<'EOF'
[Service]
WorkingDirectory=/opt/wine/current/wine_server
Environment=WINE_FRONTEND_DIST=/opt/wine/current/wine_server/static/mobile
ExecStart=
ExecStart=/opt/wine/venv/bin/python /opt/wine/current/wine_server/app.py
EOF
cat > /etc/systemd/system/wine-deploy-gateway.service <<'EOF'
[Unit]
Description=Wine GitHub OIDC deployment gateway
After=network.target
[Service]
User=wine-deploy
Group=wine-deploy
ExecStart=/opt/wine/deploy-venv/bin/python /usr/local/lib/wine-deploy/oidc_gateway.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/wine-deploy/queue
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0027
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/wine-deploy-worker.service <<'EOF'
[Unit]
Description=Wine serialized release deployment worker
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/python3 /usr/local/lib/wine-deploy/deploy_release.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
TimeoutStopSec=1100
KillSignal=SIGINT
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/wine /var/lib/wine-deploy
UMask=0022
[Install]
WantedBy=multi-user.target
EOF
python3 - <<'PY'
from pathlib import Path
import shutil,time
p=Path('/etc/nginx/sites-enabled/wine').resolve()
s=p.read_text()
if 'location ^~ /__deploy/' not in s:
    backup=Path('/opt/wine/backups')/('cicd-infrastructure-'+time.strftime('%Y%m%dT%H%M%S'))
    backup.mkdir(mode=0o700)
    shutil.copy2(p,backup/'nginx.conf')
    block='''  location ^~ /__deploy/ {
    client_max_body_size 4k;
    limit_req zone=wine_deploy_rate burst=30 nodelay;
    proxy_pass http://127.0.0.1:8120;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 20s;
    add_header Cache-Control "no-store" always;
  }
'''
    marker='    location / {\n        proxy_pass http://127.0.0.1:8100;'
    if s.count(marker)!=1: raise RuntimeError('Unexpected nginx layout; inspect before editing')
    p.write_text(s.replace(marker,block+marker))
Path('/etc/nginx/conf.d/wine-deploy-rate.conf').write_text('limit_req_zone $binary_remote_addr zone=wine_deploy_rate:1m rate=3r/s;\n')
PY
nginx -t
systemctl daemon-reload
systemctl enable --now wine-deploy-gateway.service wine-deploy-worker.service
systemctl reload nginx
# wine.service is intentionally restarted only after a verified CI release arrives.
