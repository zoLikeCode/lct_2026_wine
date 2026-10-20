#!/usr/bin/env bash
# Запуск сервиса после setup.sh. Порт открывается, когда модель загружена и прогрета (~1–2 мин).
#   bash start.sh                          # :8080
#   PORT=9000 NO_WINE_THRESHOLD=0.60 bash start.sh
set -euo pipefail
cd "$(dirname "$0")"
export HF_HOME=${HF_HOME:-$PWD/hf}
export QWEN_DIR=$PWD/qwen_finetune OMP_NUM_THREADS=${OMP_NUM_THREADS:-8} MKL_NUM_THREADS=${MKL_NUM_THREADS:-8}
exec uvicorn service.app:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
