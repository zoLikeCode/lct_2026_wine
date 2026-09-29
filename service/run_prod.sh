#!/usr/bin/env bash
# Запуск сервиса на прод-сервере (GPU с bf16, ~20 ГБ видеопамяти) из распакованного wine_prod.tar.
#
#   mkdir -p /opt/wine && tar -xf wine_prod.tar -C /opt/wine && cd /opt/wine
#   bash service/run_prod.sh                      # порт 8080
#   PORT=9000 NO_WINE_THRESHOLD=0.60 bash service/run_prod.sh
set -euo pipefail
cd "$(dirname "$0")/.."

export HF_HOME=${HF_HOME:-$PWD/hf}
export HF_HUB_ENABLE_HF_TRANSFER=1 OMP_NUM_THREADS=${OMP_NUM_THREADS:-8} MKL_NUM_THREADS=${MKL_NUM_THREADS:-8}
export QWEN_DIR=$PWD
[ -d weights ] && export QWEN_EMB_MODEL=$PWD/weights

python -c "import torch; assert torch.cuda.is_available(), 'GPU не виден'; print(torch.__version__, torch.cuda.get_device_name(0))"
pip install -q -r service/requirements.txt
[ -d weights ] || python -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3-VL-Embedding-8B')"

exec uvicorn service.app:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
