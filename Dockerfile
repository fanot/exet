ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY planner ./planner
COPY api ./api
COPY scenarios ./scenarios
COPY scripts ./scripts
COPY docs/*.md ./docs/
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
