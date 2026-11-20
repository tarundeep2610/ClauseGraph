FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

# Force Python stdout/stderr to flush immediately so logs appear live in Docker logs
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required for pypdfium2/rendering
RUN apt-get update && apt-get install -y \
    libglib2.0-0 libsm6 libxrender1 libxext6 git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY config.py .
COPY auth_core.py .
COPY auth_deps.py .
COPY auth_router.py .
COPY role_utils.py .
COPY database.py .
COPY task_queue.py .
COPY gpu_ocr_client.py .
COPY --from=frontend-build /frontend/dist ./frontend/dist

# unlimited_ocr_engine.py is intentionally NOT copied here — OCR model
# inference runs on the GPU server (see gpu-ocr-service/). It's only needed
# for local dev with OCR_LOCAL_ENGINE=true, which doesn't use this image.

# Pre-create the data directory
RUN mkdir -p /app/data/uploads

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
