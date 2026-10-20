#!/usr/bin/env bash
# Окружение на RunPod (шаблон PyTorch 2.8 + CUDA 12.8 — нужен для RTX 5090 / Blackwell).
# torch НЕ переустанавливаем: берём сборку из шаблона.
set -euo pipefail
cd "$(dirname "$0")"

export HF_HOME=${HF_HOME:-/workspace/hf}
mkdir -p "$HF_HOME"

python - <<'EOF'
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda)
assert torch.cuda.is_available(), "GPU не виден"
name = torch.cuda.get_device_name(0); cap = torch.cuda.get_device_capability(0)
print("GPU:", name, "sm_%d%d" % cap, "%.0f GB" % (torch.cuda.get_device_properties(0).total_memory / 2**30))
x = torch.randn(64, 64, device="cuda", dtype=torch.bfloat16); (x @ x).sum().item()
print("bf16 matmul OK")
EOF

[ -d Qwen3-VL-Embedding ] || git clone --depth 1 https://github.com/QwenLM/Qwen3-VL-Embedding.git

pip install -q -U "transformers>=4.57.3" "qwen-vl-utils>=0.0.14" "peft>=0.17" "accelerate>=1.12.0" \
    opencv-python-headless pillow numpy

python -c "from huggingface_hub import snapshot_download; print(snapshot_download('Qwen/Qwen3-VL-Embedding-8B'))"

python - <<'EOF'
import json; s = json.load(open("data/manifests/summary.json")); print(json.dumps(s, ensure_ascii=False, indent=1))
EOF
echo "готово. дальше: python eval.py   (оценка без обучения)"
