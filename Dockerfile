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

# Install compact CPU PyTorch first
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.1

# Install Folivora dependencies
COPY src/requirements.txt /app/src/requirements.txt
RUN pip install --no-cache-dir -r /app/src/requirements.txt

# Pre-bake open-weight TabPFN v2 foundation weights into Docker image layer during build
RUN python -c "from tabpfn import TabPFNRegressor; from tabpfn.constants import ModelVersion; import numpy as np; reg = TabPFNRegressor.create_default_for_version(ModelVersion.V2, device='cpu', n_estimators=4); reg.fit(np.zeros((24, 7), dtype=np.float32), np.zeros(24, dtype=np.float32))"

# Copy dataset, benchmarks, evaluation reports, and application source
COPY data/ /app/data/
COPY benchmarks/ /app/benchmarks/
COPY eval/ /app/eval/
COPY src/ /app/src/

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
