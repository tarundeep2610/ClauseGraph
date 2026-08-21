FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y \
    python3.11 python3-pip git \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/python3.11 /usr/bin/python

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# `pip install` above targets Ubuntu 22.04's default python3 (3.10 — matches
# the cp310 torch wheel actually downloaded), not the python3.11 we installed
# separately. Repoint `python` at python3 so it resolves to the interpreter
# that actually has the packages, instead of an empty python3.11.
RUN ln -sf /usr/bin/python3 /usr/bin/python

COPY main.py .
COPY config.py .
COPY unlimited_ocr_engine.py .

ENV HF_HOME=/app/hf_cache
RUN mkdir -p /app/hf_cache

# Dedicated non-root user — this process executes trust_remote_code from
# HuggingFace at model-load time, so it should never run as root.
RUN useradd -m -u 1000 ocrsvc && chown -R ocrsvc:ocrsvc /app
USER ocrsvc

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
