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
COPY database.py .
COPY task_queue.py .
COPY unlimited_ocr_engine.py .
COPY static/ static/

# Pre-create data and HuggingFace cache directories
RUN mkdir -p /app/data/uploads /root/.cache/huggingface/hub

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
