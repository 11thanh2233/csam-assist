#!/usr/bin/env bash
# Cài backend trên Linux / macOS / WSL.
#   bash scripts/setup.sh                 # chỉ phần chung (đủ để chạy SAM3_ENGINE=mock)
#   bash scripts/setup.sh sam3            # + PyTorch + gói sam3 chính thức của Meta
#   bash scripts/setup.sh transformers    # + PyTorch + SAM3 qua Hugging Face transformers
#   bash scripts/setup.sh skeleton        # + MediaPipe cho skeleton khuôn mặt (chạy SAU một trong hai dòng trên)
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-base}"
PYTHON="${PYTHON:-python3}"
# Đổi theo phiên bản CUDA của máy; xem lệnh cài PyTorch trong README của SAM3.
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu126}"

"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Cần Python 3.10 trở lên (SAM3 chính thức cần 3.12)"'
[ -d .venv ] || "$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ "$MODE" = "sam3" ] || [ "$MODE" = "transformers" ]; then
  pip install torch torchvision --index-url "$TORCH_INDEX"
fi
if [ "$MODE" = "sam3" ]; then
  mkdir -p third_party
  [ -d third_party/sam3 ] || git clone https://github.com/facebookresearch/sam3.git third_party/sam3
  pip install -e third_party/sam3
  pip install "huggingface_hub[cli]"
elif [ "$MODE" = "transformers" ]; then
  pip install --upgrade transformers "huggingface_hub[cli]"
fi

if [ "$MODE" = "skeleton" ]; then
  # Cài không kèm opencv-contrib-python để không đụng opencv-python-headless đang có (cả hai cùng cung cấp cv2).
  pip install --no-deps mediapipe
  pip install absl-py flatbuffers certifi sounddevice matplotlib scipy
  python -c "import mediapipe; from mediapipe.tasks.python import vision; print('MediaPipe', mediapipe.__version__, 'OK')"
fi

[ -f .env ] || cp .env.example .env
echo
echo "Xong. Bước tiếp theo:"
if [ "$MODE" != "base" ]; then
  echo "  1. Xin quyền tải model tại https://huggingface.co/facebook/sam3 rồi chạy:  hf auth login"
  echo "  2. bash scripts/run.sh"
else
  echo "  Chạy thử không cần model:  SAM3_ENGINE=mock bash scripts/run.sh"
fi
