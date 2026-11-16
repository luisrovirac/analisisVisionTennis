FROM python:3.11-slim

# mediapipe necesita en tiempo de ejecución libGL/glib (por opencv) y GLES/EGL (por sus bindings de C).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgles2 libegl1 \
 && rm -rf /var/lib/apt/lists/*

# Modelo de pose fijado a una versión concreta (no "latest") para que los resultados sean reproducibles.
ADD https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task \
    /models/pose_landmarker_full.task

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && useradd --create-home --uid 10001 worker \
 && chmod 644 /models/pose_landmarker_full.task
COPY knowledge_base ./knowledge_base

USER worker
CMD ["python", "-m", "tennis_worker.main"]
