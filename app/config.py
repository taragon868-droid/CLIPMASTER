import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JOBS = DATA / "jobs"
UPLOADS = DATA / "uploads"
STATIC = ROOT / "static"

for folder in (DATA, JOBS, UPLOADS, STATIC):
    folder.mkdir(parents=True, exist_ok=True)

HOST = os.environ.get("CLIPMASTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLIPMASTER_PORT", "8787"))

# Qwen 2.5 7B Q4 (~4.7 GB). Fits a 6 GB GTX 1060 when the speech model stays on CPU.
# A 14B model does not. num_ctx stays modest so the weights can remain on the GPU.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("CLIPMASTER_MODEL", "qwen2.5:latest")
NUM_CTX = int(os.environ.get("CLIPMASTER_NUM_CTX", "6144"))

WHISPER_MODEL = os.environ.get("CLIPMASTER_WHISPER", "small")
WHISPER_DEVICE = os.environ.get("CLIPMASTER_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("CLIPMASTER_WHISPER_COMPUTE", "int8")

MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
