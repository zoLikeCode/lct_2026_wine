#!/usr/bin/env bash
set -Eeuo pipefail

# Edit these three URLs after moving the archive to another server.
# The browser uses one origin for the frontend and API so cookies, camera and
# scan attachments work together. Put a TLS reverse proxy in front if needed.
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:8100}"
BACKEND_URL="${BACKEND_URL:-$FRONTEND_URL}"
RUNPOD_URL="${RUNPOD_URL:-https://cjrcewr46hi8eo-8080.proxy.runpod.net}"

# The address on which this process listens. Use 0.0.0.0 for direct access;
# keep 127.0.0.1 when an HTTPS reverse proxy runs on this server.
BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-127.0.0.1}"
BACKEND_BIND_PORT="${BACKEND_BIND_PORT:-8100}"
# Keep data outside backend/ and dist/ so code upgrades cannot replace it.
DATA_ROOT="${DATA_ROOT:-}"
# Optional direct HTTPS. If unset, use a reverse proxy for a public domain.
TLS_CERT_FILE="${TLS_CERT_FILE:-}"
TLS_KEY_FILE="${TLS_KEY_FILE:-}"
TRUSTED_PROXY_IPS="${TRUSTED_PROXY_IPS:-127.0.0.1}"

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DATA_ROOT="${DATA_ROOT:-$ROOT/data}"
PYTHON_BIN="${WINE_PYTHON:-${PYTHON_BIN:-python3}}"
SECRETS_FILE="${WINE_ENV_FILE:-$ROOT/.env}"
umask 077

fail() { printf 'Ошибка запуска: %s\n' "$*" >&2; exit 1; }
[[ -d "$ROOT/backend" && -f "$ROOT/dist/index.html" && -f "$ROOT/seed/catalog.csv" ]] || fail 'Архив неполный: нужны backend/, dist/ и seed/catalog.csv.'
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail 'Нужен Python 3.10+ с venv и pip.'
"$PYTHON_BIN" -c 'import sys;sys.exit(0 if (3,10)<=sys.version_info[:2]<(3,14) else 1)' || fail 'Нужен Python версии 3.10–3.13.'
"$PYTHON_BIN" - "$FRONTEND_URL" "$BACKEND_URL" "$RUNPOD_URL" "$BACKEND_BIND_PORT" <<'PY' || fail 'Проверьте URL и порт в начале start.sh.'
import sys
from urllib.parse import urlsplit
front, back, pod, port = sys.argv[1:]
def origin(value):
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.path not in ('', '/') or p.query or p.fragment:
        raise ValueError('URL must be an HTTP(S) origin without path or credentials')
    return (p.scheme, p.hostname.lower(), p.port or (443 if p.scheme == 'https' else 80))
if origin(front) != origin(back):
    raise ValueError('Frontend and API must use the same public origin')
p = urlsplit(pod)
if not ((p.scheme == 'https' and p.hostname) or (p.scheme == 'http' and p.hostname == '127.0.0.1' and p.port)) or p.path not in ('', '/') or p.username or p.password or p.query or p.fragment:
    raise ValueError('RunPod URL must be HTTPS or a loopback HTTP tunnel')
if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise ValueError('Invalid backend port')
PY
DATA_ROOT="$("$PYTHON_BIN" - "$DATA_ROOT" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"

if [[ -f "$SECRETS_FILE" ]]; then
  [[ ! -L "$SECRETS_FILE" ]] || fail 'Файл секретов не должен быть символической ссылкой.'
  set -a
  # This is a private, administrator-owned shell file created after unzip.
  # shellcheck disable=SC1090
  source "$SECRETS_FILE"
  set +a
fi
if [[ -z "${WINE_ADMIN_PASSWORD:-}" ]]; then
  admin_password="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  mkdir -p -- "$(dirname -- "$SECRETS_FILE")"
  printf '\nWINE_ADMIN_PASSWORD=%s\n' "$admin_password" >> "$SECRETS_FILE"
  chmod 600 "$SECRETS_FILE"
  export WINE_ADMIN_PASSWORD="$admin_password"
  if [[ -t 1 ]]; then printf 'Новый пароль админки: %s\n' "$admin_password"; fi
  unset admin_password
fi

if [[ -n "${WINE_GIGACHAT_AUTH_KEY:-}" || -n "${WINE_GIGACHAT_CLIENT_ID:-}" ]]; then
  [[ -n "${WINE_GIGACHAT_AUTH_KEY:-}" && -n "${WINE_GIGACHAT_CLIENT_ID:-}" ]] || fail 'Для GigaChat нужны и WINE_GIGACHAT_AUTH_KEY, и WINE_GIGACHAT_CLIENT_ID в приватном .env.'
  export WINE_ASSISTANT_PROVIDER=gigachat
  export WINE_GIGACHAT_SCOPE="${WINE_GIGACHAT_SCOPE:-GIGACHAT_API_PERS}"
  export WINE_ASSISTANT_MODEL="${WINE_ASSISTANT_MODEL:-GigaChat-2}"
  export WINE_ASSISTANT_VISION_MODEL="${WINE_ASSISTANT_VISION_MODEL:-GigaChat-2-Pro}"
else
  printf 'GigaChat не настроен; помощник использует подбор по каталогу.\n' >&2
fi

export WINE_DATASET="$DATA_ROOT/wine_dataset"
export WINE_FRONTEND_DIST="$ROOT/dist"
export WINE_INFERENCE_BACKEND=runpod
export WINE_ML_URL="${RUNPOD_URL%/}"
if [[ "$FRONTEND_URL" == https://* ]]; then export WINE_COOKIE_SECURE=1; else export WINE_COOKIE_SECURE=0; fi
export PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=2

if [[ -n "${WINE_PYTHON:-}" ]]; then
  [[ -x "$WINE_PYTHON" ]] || fail 'WINE_PYTHON указывает на несуществующий интерпретатор.'
  RUNTIME_PYTHON="$(cd -- "$(dirname -- "$WINE_PYTHON")" && pwd -P)/$(basename -- "$WINE_PYTHON")"
  "$RUNTIME_PYTHON" -c 'import fastapi,uvicorn,multipart,PIL,requests' || fail 'В WINE_PYTHON отсутствуют серверные зависимости.'
else
  if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$ROOT/.venv" || fail 'Не удалось создать venv. Установите пакет python3-venv.'
  fi
  required_hash="$("$PYTHON_BIN" - "$ROOT/requirements.txt" <<'PY'
import hashlib,sys
print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())
PY
)"
  if [[ ! -f "$ROOT/.venv/.requirements.sha256" || "$(cat "$ROOT/.venv/.requirements.sha256")" != "$required_hash" ]]; then
    "$ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt" || fail 'Не удалось установить Python-зависимости.'
    printf '%s\n' "$required_hash" > "$ROOT/.venv/.requirements.sha256"
  fi
  RUNTIME_PYTHON="$ROOT/.venv/bin/python"
fi
"$RUNTIME_PYTHON" "$ROOT/bootstrap_dataset.py" --dataset "$WINE_DATASET" --catalog "$ROOT/seed/catalog.csv" || fail 'Не удалось подготовить базу и каталог.'

if [[ -n "$TLS_CERT_FILE" || -n "$TLS_KEY_FILE" ]]; then
  [[ -f "$TLS_CERT_FILE" && -f "$TLS_KEY_FILE" ]] || fail 'Для прямого HTTPS укажите существующие TLS_CERT_FILE и TLS_KEY_FILE.'
  TLS_CERT_FILE="$("$PYTHON_BIN" -c 'from pathlib import Path;import sys;print(Path(sys.argv[1]).resolve())' "$TLS_CERT_FILE")"
  TLS_KEY_FILE="$("$PYTHON_BIN" -c 'from pathlib import Path;import sys;print(Path(sys.argv[1]).resolve())' "$TLS_KEY_FILE")"
fi
printf 'Фронтенд: %s\nAPI: %s/api/\nRunPod: %s\nСлушает: %s:%s\nДанные: %s\n' \
  "$FRONTEND_URL" "${BACKEND_URL%/}" "$RUNPOD_URL" "$BACKEND_BIND_HOST" "$BACKEND_BIND_PORT" "$DATA_ROOT"
if [[ "$FRONTEND_URL" == https://* && -z "$TLS_CERT_FILE" ]]; then
  printf 'Для HTTPS настройте обратный прокси от %s к http://%s:%s.\n' "$FRONTEND_URL" "$BACKEND_BIND_HOST" "$BACKEND_BIND_PORT" >&2
fi
if [[ "$FRONTEND_URL" == http://* && "$FRONTEND_URL" != http://127.0.0.1:* && "$FRONTEND_URL" != http://localhost:* ]]; then
  printf 'Для камеры и установки PWA на удалённом устройстве нужен HTTPS.\n' >&2
fi
cd -- "$ROOT/backend"
if [[ -n "$TLS_CERT_FILE" ]]; then
  exec "$RUNTIME_PYTHON" -m uvicorn app:create_app --factory \
    --host "$BACKEND_BIND_HOST" --port "$BACKEND_BIND_PORT" \
    --proxy-headers --forwarded-allow-ips "$TRUSTED_PROXY_IPS" \
    --ssl-certfile "$TLS_CERT_FILE" --ssl-keyfile "$TLS_KEY_FILE"
fi
exec "$RUNTIME_PYTHON" -m uvicorn app:create_app --factory \
  --host "$BACKEND_BIND_HOST" --port "$BACKEND_BIND_PORT" \
  --proxy-headers --forwarded-allow-ips "$TRUSTED_PROXY_IPS"
