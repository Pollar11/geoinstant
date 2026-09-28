# syntax=docker/dockerfile:1.7
# CPU image by default. For NVIDIA GPUs:
#   docker build --build-arg BASE=nvcr.io/nvidia/tensorrt:24.08-py3 --build-arg ORT=onnxruntime-gpu -t geoinstant-inference:gpu .
ARG BASE=python:3.11-slim
FROM ${BASE} AS runtime

ARG ORT=onnxruntime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GEOINSTANT_ARTIFACTS_DIR=/artifacts \
    GEOINSTANT_FEEDBACK_DIR=/feedback \
    # Persist TensorRT engines so restarts don't rebuild them (cold start < 2 s).
    ORT_TENSORRT_ENGINE_CACHE_ENABLE=1 \
    ORT_TENSORRT_CACHE_PATH=/artifacts/trt-cache \
    ORT_TENSORRT_FP16_ENABLE=1

WORKDIR /app
COPY pyproject.toml ./
COPY geoinstant ./geoinstant
RUN pip install . "${ORT}" "rapidocr_onnxruntime>=1.3" && \
    if [ "${ORT}" != "onnxruntime" ]; then pip uninstall -y onnxruntime || true; fi

RUN useradd --create-home --uid 10001 app && mkdir -p /artifacts /feedback && chown app /artifacts /feedback
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status != 200)"
# One worker per GPU: models are loaded per process. Scale out with replicas, not workers.
CMD ["uvicorn", "geoinstant.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "30"]
