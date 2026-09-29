#!/usr/bin/env bash
# Установка сервиса распознавания вина с нуля после git clone (Linux + NVIDIA GPU >= 24 ГБ, CUDA 12.x,
# torch с CUDA уже установлен — например, образ RunPod PyTorch 2.8).
#
#   git clone https://github.com/zoLikeCode/lct_2025_wine.git && cd lct_2025_wine
#   bash setup.sh          # зависимости, адаптер + индекс (Google Drive), веса модели (HuggingFace)
#   bash start.sh          # сервис на :8080, контракт POST /v1/eval/predict -> {"slug": ...}
set -euo pipefail
cd "$(dirname "$0")"

# wine_prod.tar: LoRA-адаптер, индекс 2103 вин, карточки вин, код Qwen3-VL-Embedding (service/make_bundle.sh)
ASSETS_ID=${ASSETS_ID:-130WubX1vk54z5MWeRNBOwdqp70sz1FPZ}
ASSETS_MD5=c6cdc05a1508c968813682f4cf3ca59c
Q=qwen_finetune
export HF_HOME=${HF_HOME:-$PWD/hf}
export HF_HUB_ENABLE_HF_TRANSFER=1

python - <<'EOF'
import torch
assert torch.cuda.is_available(), "GPU не виден"
p = torch.cuda.get_device_properties(0)
print("torch", torch.__version__, "|", p.name, "%.0f GB" % (p.total_memory / 2**30))
EOF

pip install -q gdown hf_transfer
pip install -q -r service/requirements.txt

if [ ! -f "$Q/results/index_prod.npz" ]; then
    [ -f wine_prod.tar ] || gdown "$ASSETS_ID" -O wine_prod.tar
    echo "$ASSETS_MD5  wine_prod.tar" | md5sum -c -
    tar -xf wine_prod.tar -C "$Q" adapter results/index_prod.npz catalog_cards.json Qwen3-VL-Embedding
fi

python -c "from huggingface_hub import snapshot_download; print('веса:', snapshot_download('Qwen/Qwen3-VL-Embedding-8B'))"
echo "готово. запуск: bash start.sh"
