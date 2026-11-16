# Servidor de MLflow: la imagen oficial no trae boto3, necesario para guardar artefactos en MinIO/R2.
FROM python:3.11-slim
# sqlalchemy<2.1: mlflow 2.22 importa FallbackAsyncAdaptedQueuePool, que SQLAlchemy 2.1 elimino.
RUN pip install --no-cache-dir "mlflow==2.22.0" "sqlalchemy>=2.0,<2.1" "boto3>=1.34,<2" \
 && useradd --create-home --uid 10001 mlflow \
 && mkdir /mlflow && chown mlflow /mlflow
USER mlflow
EXPOSE 5000
# Metadatos en SQLite (volumen); artefactos en el bucket de ML, servidos a través del propio servidor
# (--serve-artifacts): los clientes no necesitan credenciales del bucket para leerlos.
CMD ["sh", "-c", "mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri sqlite:////mlflow/mlflow.db --artifacts-destination s3://${ML_S3_BUCKET}/mlflow --serve-artifacts"]
