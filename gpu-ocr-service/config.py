import os

GPU_OCR_SHARED_SECRET = os.environ.get("GPU_OCR_SHARED_SECRET", "")
GPU_OCR_MAX_CONCURRENT_JOBS = int(os.environ.get("GPU_OCR_MAX_CONCURRENT_JOBS", "2"))

if not GPU_OCR_SHARED_SECRET:
    raise RuntimeError(
        "GPU_OCR_SHARED_SECRET must be set — this service refuses to start without it, "
        "since it is the only credential protecting the model-inference API."
    )
