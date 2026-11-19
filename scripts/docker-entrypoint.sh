#!/bin/sh
# Lần chạy đầu: chép guideline mẫu vào volume. Bài học và các lượt soạn nằm trong volume nên không mất khi cập nhật image.
set -e
mkdir -p "$SAM3_LIBRARY_DIR"
cp -rn /app/guideline_library.seed/. "$SAM3_LIBRARY_DIR"/ 2>/dev/null || true
# knowledge.yaml và guideline mẫu đi theo image; lessons.jsonl và runs/ là của bạn, không bị ghi đè.
cp /app/guideline_library.seed/knowledge.yaml "$SAM3_LIBRARY_DIR"/knowledge.yaml
exec "$@"
