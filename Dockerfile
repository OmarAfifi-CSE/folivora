FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Folivora dependencies (slim: no torch/TabPFN — the image runs the
# HistGradientBoosting quantile fallback, see src/tabpfn_engine.py).
# To ship the full TabPFN engine instead, add src/requirements-ml.txt here,
# drop FOLIVORA_ML_MODE below, and restore the CPU PyTorch pre-install step.
COPY src/requirements.txt /app/src/requirements.txt
RUN pip install --no-cache-dir -r /app/src/requirements.txt

ENV FOLIVORA_ML_MODE=fallback

# Copy dataset, benchmarks, evaluation reports, browser extension, and application source
COPY data/ /app/data/
COPY benchmarks/ /app/benchmarks/
COPY eval/ /app/eval/
COPY chrome_extension/ /app/chrome_extension/
COPY src/ /app/src/

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
