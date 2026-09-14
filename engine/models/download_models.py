"""
Peek Face Engine - Model Downloader
Downloads lightweight pre-trained SCRFD detector and ArcFace MobileFaceNet embedder models.
"""

import os
import io
import zipfile
import urllib.request
import logging

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.DownloadModels")

MODEL_ZIP_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip"
MODELS_DIR = os.path.dirname(os.path.abspath(__file__))

DETECTOR_NAME = "scrfd_500m_bnkps.onnx"
EMBEDDER_NAME = "w600k_mbf.onnx"

def ensure_models_exist():
    """
    Ensures that the required ONNX models are present in the models directory.
    If missing, downloads buffalo_sc.zip from InsightFace official release and extracts them.
    """
    detector_path = os.path.join(MODELS_DIR, DETECTOR_NAME)
    embedder_path = os.path.join(MODELS_DIR, EMBEDDER_NAME)

    if os.path.exists(detector_path) and os.path.exists(embedder_path):
        logger.info(f"Models already exist at {MODELS_DIR}")
        return detector_path, embedder_path

    logger.info(f"Downloading models package from {MODEL_ZIP_URL}...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Peek/1.0"}
    req = urllib.request.Request(MODEL_ZIP_URL, headers=headers)
    
    with urllib.request.urlopen(req) as response:
        total_length = response.headers.get("Content-Length")
        total_size = int(total_length) if total_length else None
        logger.info(f"Downloading {total_size / (1024*1024):.1f} MB..." if total_size else "Downloading...")
        zip_bytes = response.read()

    logger.info("Extracting ONNX models...")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for file_info in z.infolist():
            filename = file_info.filename
            if filename == "det_500m.onnx":
                with z.open(filename) as src, open(detector_path, "wb") as dst:
                    dst.write(src.read())
                logger.info(f"Saved {DETECTOR_NAME} ({os.path.getsize(detector_path)} bytes)")
            elif filename == "w600k_mbf.onnx":
                with z.open(filename) as src, open(embedder_path, "wb") as dst:
                    dst.write(src.read())
                logger.info(f"Saved {EMBEDDER_NAME} ({os.path.getsize(embedder_path)} bytes)")

    if not os.path.exists(detector_path) or not os.path.exists(embedder_path):
        raise RuntimeError("Failed to download and extract required models.")

    logger.info("Model download and verification complete.")
    return detector_path, embedder_path

if __name__ == "__main__":
    ensure_models_exist()
