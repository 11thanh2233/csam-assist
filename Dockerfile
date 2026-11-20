# Backend SAM3 Label Assist trong một container.
#   docker compose up --build            (GPU NVIDIA, xem docker-compose.yml)
#   docker build --build-arg VARIANT=mock -t sam3-assist:mock .   (không model, chỉ để thử giao diện)
FROM python:3.12-slim

ARG VARIANT=gpu
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu126

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    SAM3_HOST=0.0.0.0 SAM3_PORT=8765 \
    HF_HOME=/data/huggingface \
    SAM3_LIBRARY_DIR=/data/guideline_library \
    SAM3_FACE_MODEL=/data/models/face_landmarker.task

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# PyTorch + SAM3 (qua transformers) + MediaPipe cho skeleton khuôn mặt.
# MediaPipe cài không kèm opencv-contrib-python để không đụng opencv-python-headless.
RUN if [ "$VARIANT" = "gpu" ]; then \
      pip install torch torchvision --index-url "$TORCH_INDEX" && \
      pip install --upgrade transformers "huggingface_hub[cli]" && \
      pip install --no-deps mediapipe && \
      pip install absl-py flatbuffers certifi sounddevice matplotlib scipy ; \
    fi

COPY backend ./backend
COPY profiles ./profiles
COPY userscript ./userscript
COPY guideline_library ./guideline_library.seed
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8765
VOLUME ["/data"]
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "backend"]
