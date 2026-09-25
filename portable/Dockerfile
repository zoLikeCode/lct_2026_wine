FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY portable/requirements.txt /app/portable/requirements.txt
COPY portable/requirements-tested.txt /app/portable/requirements-tested.txt
RUN pip install --no-cache-dir -r portable/requirements.txt
COPY portable /app/portable
ENV WINE_MODEL_DIR=/models
EXPOSE 8080
CMD ["uvicorn", "portable.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
