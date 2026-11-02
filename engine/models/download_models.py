"""
Peek Face Engine - Model Downloader
Downloads lightweight pre-trained SCRFD detector and ArcFace MobileFaceNet embedder models.
"""

import os
import io
import zipfile
import urllib.request
import logging
from typing import Optional, Tuple

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.DownloadModels")

MODEL_ZIP_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip"
MODELS_DIR = os.path.dirname(os.path.abspath(__file__))

DETECTOR_NAME = "scrfd_500m_bnkps.onnx"
EMBEDDER_NAME = "w600k_mbf.onnx"
SEGMENTATION_NAME = "selfie_segmentation.onnx"
SEGMENTATION_URL = "https://huggingface.co/webml/models-moved/resolve/feeede7c6038e02f8dd5be2116c9c8e32967b406/selfie-segmentation-general.onnx"


def ensure_segmentation_model() -> Optional[str]:
    """
    Ensures that the selfie segmentation ONNX model is present in the models directory.
    Downloads the model if missing. Returns None if download fails (allowing fallback).
    """
    seg_path = os.path.join(MODELS_DIR, SEGMENTATION_NAME)
    if os.path.exists(seg_path) and os.path.getsize(seg_path) > 100000:
        return seg_path

    try:
        logger.info(f"Downloading selfie segmentation model from {SEGMENTATION_URL}...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Peek/1.0"}
        req = urllib.request.Request(SEGMENTATION_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()
            if len(content) > 100000:
                with open(seg_path, "wb") as f:
                    f.write(content)
                logger.info(f"Saved {SEGMENTATION_NAME} ({len(content)} bytes)")
                return seg_path
    except Exception as ex:
        logger.warning("Could not download selfie segmentation model: %s. Fallback will be used.", ex)
    return seg_path if os.path.exists(seg_path) else None

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
