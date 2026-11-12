"""
Peek Face Engine - Model Downloader
Downloads and verifies pre-trained SCRFD detector, ArcFace MobileFaceNet embedder,
and MediaPipe selfie segmentation models with cryptographic SHA-256 hash pinning.
Cached downloads are stored under build/models/.
"""

import os
import io
import shutil
import hashlib
import zipfile
import urllib.request
import logging
from typing import Optional, Tuple

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("Peek.DownloadModels")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(REPO_ROOT, "build", "models")
MODELS_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_ZIP_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip"
SEGMENTATION_URL = "https://huggingface.co/webml/models-moved/resolve/feeede7c6038e02f8dd5be2116c9c8e32967b406/selfie-segmentation-general.onnx"

DETECTOR_NAME = "scrfd_500m_bnkps.onnx"
EMBEDDER_NAME = "w600k_mbf.onnx"
SEGMENTATION_NAME = "selfie_segmentation.onnx"

# Cryptographic SHA-256 hashes pinned for supply-chain integrity
PINNED_HASHES = {
    "buffalo_sc.zip": "57d31b56b6ffa911c8a73cfc1707c73cab76efe7f13b675a05223bf42de47c72",
    DETECTOR_NAME: "5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a",
    EMBEDDER_NAME: "9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f",
    SEGMENTATION_NAME: "de212dabbc6266f0047711d1dfae80900f7b596b9ed5f7665f3d1cf68c5443ee",
}


def compute_sha256(file_path: str) -> str:
    """Computes the lowercase hexadecimal SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def verify_sha256(file_path: str, expected_hash: str, description: str) -> bool:
    """Verifies that a file's SHA-256 matches the expected hash. Raises ValueError on mismatch."""
    if not os.path.exists(file_path):
        return False
    actual_hash = compute_sha256(file_path)
    if actual_hash != expected_hash.lower():
        raise ValueError(
            f"Cryptographic hash mismatch for {description} ({file_path}):\n"
            f"  Expected: {expected_hash.lower()}\n"
            f"  Actual:   {actual_hash}\n"
            "Aborting for supply chain integrity."
        )
    return True


def ensure_segmentation_model() -> Optional[str]:
    """
    Ensures that the selfie segmentation ONNX model is present and matches its pinned SHA-256 hash.
    Checks build/models cache before downloading.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    seg_target = os.path.join(MODELS_DIR, SEGMENTATION_NAME)
    seg_cache = os.path.join(CACHE_DIR, SEGMENTATION_NAME)
    expected_hash = PINNED_HASHES[SEGMENTATION_NAME]

    # 1. Check if model already in target directory and hash is valid
    if os.path.exists(seg_target):
        try:
            if verify_sha256(seg_target, expected_hash, SEGMENTATION_NAME):
                logger.info(f"Verified existing {SEGMENTATION_NAME} in models dir.")
                return seg_target
        except ValueError:
            logger.warning(f"Corrupt {SEGMENTATION_NAME} detected in models dir; removing.")
            os.remove(seg_target)

    # 2. Check cache in build/models
    if os.path.exists(seg_cache):
        try:
            if verify_sha256(seg_cache, expected_hash, f"cached {SEGMENTATION_NAME}"):
                logger.info(f"Restoring {SEGMENTATION_NAME} from cache {seg_cache}")
                shutil.copy2(seg_cache, seg_target)
                return seg_target
        except ValueError:
            logger.warning(f"Corrupt cached {SEGMENTATION_NAME} detected; removing.")
            os.remove(seg_cache)

    # 3. Download from mirror into cache, verify hash, and copy to target
    logger.info(f"Downloading selfie segmentation model from {SEGMENTATION_URL}...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Peek/1.0"}
    req = urllib.request.Request(SEGMENTATION_URL, headers=headers)

    tmp_download = seg_cache + ".tmp"
    try:
        with urllib.request.urlopen(req, timeout=60) as response, open(tmp_download, "wb") as out:
            shutil.copyfileobj(response, out)

        verify_sha256(tmp_download, expected_hash, f"downloaded {SEGMENTATION_NAME}")
        os.replace(tmp_download, seg_cache)
        shutil.copy2(seg_cache, seg_target)
        logger.info(f"Successfully saved and verified {SEGMENTATION_NAME}.")
        return seg_target
    except Exception as ex:
        if os.path.exists(tmp_download):
            os.remove(tmp_download)
        raise RuntimeError(f"Failed to acquire selfie segmentation model: {ex}") from ex


def ensure_models_exist() -> Tuple[str, str]:
    """
    Ensures that SCRFD detector and MobileFaceNet embedder models are present and match pinned SHA-256.
    Checks build/models/buffalo_sc.zip cache before downloading.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    detector_path = os.path.join(MODELS_DIR, DETECTOR_NAME)
    embedder_path = os.path.join(MODELS_DIR, EMBEDDER_NAME)
    zip_cache = os.path.join(CACHE_DIR, "buffalo_sc.zip")

    # 1. Check if both models exist in target and match pinned hashes
    models_ok = True
    if os.path.exists(detector_path):
        try:
            verify_sha256(detector_path, PINNED_HASHES[DETECTOR_NAME], DETECTOR_NAME)
        except ValueError:
            logger.warning(f"Invalid {DETECTOR_NAME} in models dir; removing.")
            os.remove(detector_path)
            models_ok = False
    else:
        models_ok = False

    if os.path.exists(embedder_path):
        try:
            verify_sha256(embedder_path, PINNED_HASHES[EMBEDDER_NAME], EMBEDDER_NAME)
        except ValueError:
            logger.warning(f"Invalid {EMBEDDER_NAME} in models dir; removing.")
            os.remove(embedder_path)
            models_ok = False
    else:
        models_ok = False

    if models_ok:
        logger.info(f"Verified all required face models at {MODELS_DIR}")
        return detector_path, embedder_path

    # 2. Check if valid buffalo_sc.zip exists in cache
    zip_valid = False
    if os.path.exists(zip_cache):
        try:
            verify_sha256(zip_cache, PINNED_HASHES["buffalo_sc.zip"], "cached buffalo_sc.zip")
            zip_valid = True
            logger.info("Using cached buffalo_sc.zip from build/models.")
        except ValueError:
            logger.warning("Corrupt cached buffalo_sc.zip; removing.")
            os.remove(zip_cache)

    # 3. Download buffalo_sc.zip if not in cache
    if not zip_valid:
        logger.info(f"Downloading models package from {MODEL_ZIP_URL}...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Peek/1.0"}
        req = urllib.request.Request(MODEL_ZIP_URL, headers=headers)

        tmp_zip = zip_cache + ".tmp"
        try:
            with urllib.request.urlopen(req, timeout=120) as response, open(tmp_zip, "wb") as out:
                shutil.copyfileobj(response, out)

            verify_sha256(tmp_zip, PINNED_HASHES["buffalo_sc.zip"], "downloaded buffalo_sc.zip")
            os.replace(tmp_zip, zip_cache)
            logger.info("Successfully downloaded and verified buffalo_sc.zip.")
        except Exception as ex:
            if os.path.exists(tmp_zip):
                os.remove(tmp_zip)
            raise RuntimeError(f"Failed to download models package: {ex}") from ex

    # 4. Extract models from cached zip
    logger.info("Extracting models from cached buffalo_sc.zip...")
    with zipfile.ZipFile(zip_cache, "r") as z:
        for file_info in z.infolist():
            filename = file_info.filename
            if filename == "det_500m.onnx":
                with z.open(filename) as src, open(detector_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                verify_sha256(detector_path, PINNED_HASHES[DETECTOR_NAME], DETECTOR_NAME)
                logger.info(f"Extracted and verified {DETECTOR_NAME} ({os.path.getsize(detector_path)} bytes)")
            elif filename == "w600k_mbf.onnx":
                with z.open(filename) as src, open(embedder_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                verify_sha256(embedder_path, PINNED_HASHES[EMBEDDER_NAME], EMBEDDER_NAME)
                logger.info(f"Extracted and verified {EMBEDDER_NAME} ({os.path.getsize(embedder_path)} bytes)")

    if not os.path.exists(detector_path) or not os.path.exists(embedder_path):
        raise RuntimeError("Failed to extract all required face models from package.")

    return detector_path, embedder_path


if __name__ == "__main__":
    ensure_models_exist()
    ensure_segmentation_model()
    print("All models verified successfully.")
