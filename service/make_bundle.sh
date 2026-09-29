#!/usr/bin/env bash
# Собирает прод-пакет из папки пода (/workspace/qwen_hires) после build_index.py и проверки.
# Внутри всё, что нужно сервису, кроме весов Qwen3-VL-Embedding-8B (~17 ГБ, качаются с HF
# при первом запуске; WITH_WEIGHTS=1 — положить их в пакет для сервера без интернета).
#
#   bash service/make_bundle.sh            # -> /workspace/wine_prod.tar
set -euo pipefail
cd "$(dirname "$0")/.."

for f in common.py adapter/adapter_model.safetensors results/index_prod.npz Qwen3-VL-Embedding/src/models/qwen3_vl_embedding.py; do
    [ -e "$f" ] || { echo "нет $f — сначала setup.sh и build_index.py"; exit 1; }
done

OUT=${OUT:-/workspace/wine_prod.tar}
ITEMS=(common.py adapter results/index_prod.npz Qwen3-VL-Embedding service)
[ -f catalog_cards.json ] && ITEMS+=(catalog_cards.json)
if [ "${WITH_WEIGHTS:-0}" = "1" ]; then
    python -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3-VL-Embedding-8B', local_dir='weights')"
    ITEMS+=(weights)
fi
tar --exclude=__pycache__ --exclude=.git --exclude="*.jpg" --exclude="*.png" --exclude="*.mp4" -cf "$OUT" "${ITEMS[@]}"
md5sum "$OUT"
ls -lh "$OUT"
