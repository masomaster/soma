# Soma Streamlit dashboard — build context is the repository root:
#   docker build -f dashboard/Dockerfile .
FROM python:3.14-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY pipeline ./pipeline
COPY dashboard ./dashboard

RUN pip install --no-cache-dir -e ".[dashboard]" boto3>=1.34

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "dashboard/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
