# Entorno de entrenamiento: Linux para que torch/onnx/onnxruntime no dependan del runtime de
# Visual C++ del host. El código se monta en /workspace (PYTHONPATH) para iterar sin reconstruir.
# Contexto de build: la raíz del repositorio (ver deploy/docker-compose.yml, servicio ml).
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgles2 libegl1 git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'

# El mismo modelo de pose que el worker: las poses de entrenamiento salen del mismo estimador.
ADD https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task \
    /models/pose_landmarker_full.task
RUN chmod 644 /models/pose_landmarker_full.task

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY worker/pyproject.toml /tmp/worker/pyproject.toml
COPY worker/src /tmp/worker/src
COPY ml/pyproject.toml /tmp/ml/pyproject.toml
COPY ml/src /tmp/ml/src
ARG WITH_YOLO=false
RUN pip install --no-cache-dir "/tmp/worker[dev]" \
 && pip install --no-cache-dir "/tmp/ml[dev]" \
 && if [ "$WITH_YOLO" = "true" ]; then pip install --no-cache-dir "/tmp/ml[yolo]"; fi

ENV PYTHONPATH=/workspace/ml/src:/workspace/worker/src
WORKDIR /workspace/ml
CMD ["dvc", "repro"]
