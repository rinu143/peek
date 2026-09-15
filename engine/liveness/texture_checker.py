"""
Peek Face Engine - Texture & Screen Moiré Anti-Spoofing Checker
Analyzes spatial frequency, specular highlights, and chrominance dispersion to detect:
1. Screen replay attacks (LCD/OLED pixel matrix, moiré interference fringes).
2. Paper/glossy photograph print attacks (specular reflection, unnatural color cast).
3. Realistic diffuse skin scattering.
"""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class TextureLivenessResult:
    is_live: bool
    score: float                        # 0.0 (spoof texture) to 1.0 (natural skin)
    moire_detected: bool = False        # Screen pixel grid / moire interference
    specular_glare_detected: bool = False # Glass screen / glossy paper reflection
    spoof_reason: Optional[str] = None
    details: str = ""


class TextureChecker:
    """
    Evaluates visual texture frequency, reflection gradients, and skin color dispersion
    to identify screen replay and printout presentation attacks.
    """

    def __init__(
        self,
        moire_peak_threshold: float = 100.0,
        specular_ratio_threshold: float = 0.20
    ):
        self.moire_peak_threshold = moire_peak_threshold
        self.specular_ratio_threshold = specular_ratio_threshold

    def evaluate(
        self,
        frame: np.ndarray,
        bbox: Tuple[float, float, float, float]
    ) -> TextureLivenessResult:
        """
        Evaluates face texture quality and anti-spoof characteristics.
        
        Args:
            frame: Full BGR frame
            bbox: Bounding box (x1, y1, x2, y2)
        """
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]

        # Clamp to frame bounds
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(0, min(x2, w))
        y2 = max(0, min(y2, h))

        if (x2 - x1) < 40 or (y2 - y1) < 40:
            return TextureLivenessResult(
                is_live=False,
                score=0.4,
                details="Face crop too small for texture analysis"
            )

        face_crop = frame[y1:y2, x1:x2]

        # Extract inner face region (inner 60% of bbox) to eliminate background window grills,
        # room fixtures, hair, and clothing boundaries from frequency analysis
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        half_w = max(16, int((x2 - x1) * 0.30))
        half_h = max(16, int((y2 - y1) * 0.30))

        ix1 = max(0, cx - half_w)
        iy1 = max(0, cy - half_h)
        ix2 = min(w, cx + half_w)
        iy2 = min(h, cy + half_h)

        inner_crop = frame[iy1:iy2, ix1:ix2]
        if inner_crop.shape[0] < 20 or inner_crop.shape[1] < 20:
            inner_crop = face_crop

        gray_inner = cv2.cvtColor(inner_crop, cv2.COLOR_BGR2GRAY)
        std_crop = cv2.resize(gray_inner, (128, 128), interpolation=cv2.INTER_LINEAR)

        # --- Check 1: 2D FFT Frequency Spectrum (Moiré & Grid Detection) ---
        # Compute 2D Fourier transform on inner facial skin
        f_transform = np.fft.fft2(std_crop)
        f_shift = np.fft.fftshift(f_transform)
        magnitude_spectrum = np.abs(f_shift)

        # Mask out DC and low-frequency components (center 32x32)
        cx_fft, cy_fft = 64, 64
        r = 16
        high_freq_spectrum = magnitude_spectrum.copy()
        high_freq_spectrum[cy_fft - r:cy_fft + r, cx_fft - r:cx_fft + r] = 0.0

        # Screens produce discrete delta spikes in the high-frequency band.
        # Compare maximum harmonic spike against the mean high-frequency energy.
        hf_mean = float(np.mean(high_freq_spectrum))
        hf_peak = float(np.max(high_freq_spectrum))
        peak_to_mean_ratio = (hf_peak / max(hf_mean, 1e-3)) if hf_mean > 0 else 0.0

        is_moire = peak_to_mean_ratio > self.moire_peak_threshold

        # --- Check 2: Specular Reflection / Screen Glare Analysis ---
        # Look for clusters of saturated pixels with steep gradient borders
        # (characteristic of glass screen reflections and glossy photo paper)
        hsv_crop = cv2.cvtColor(face_crop, cv2.COLOR_BGR2HSV)
        v_channel = hsv_crop[:, :, 2]
        saturated_pixels = (v_channel >= 250)
        specular_ratio = float(np.sum(saturated_pixels)) / float(v_channel.size)

        is_specular = False
        if specular_ratio > self.specular_ratio_threshold:
            # Check edge gradient around saturated area
            sobel_v = cv2.Sobel(v_channel, cv2.CV_64F, 1, 1, ksize=3)
            edge_around_saturated = np.mean(np.abs(sobel_v[saturated_pixels]))
            if edge_around_saturated > 50.0:
                is_specular = True

        # --- Check 3: Chrominance Distribution (Natural Skin Check) ---
        ycrcb_crop = cv2.cvtColor(face_crop, cv2.COLOR_BGR2YCrCb)
        cr = ycrcb_crop[:, :, 1]
        cb = ycrcb_crop[:, :, 2]

        # Natural human skin boundaries in YCrCb
        skin_mask = (cr >= 130) & (cr <= 175) & (cb >= 75) & (cb <= 130)
        skin_fraction = float(np.sum(skin_mask)) / float(skin_mask.size)

        # Compute texture score
        texture_score = 1.0

        if is_moire:
            texture_score -= 0.60
        if is_specular:
            texture_score -= 0.40

        # Penalize if color distribution diverges severely from natural skin (e.g. grayscale printout)
        if skin_fraction < 0.08:
            texture_score -= 0.40
        elif skin_fraction >= 0.30:
            texture_score += 0.10

        texture_score = float(np.clip(texture_score, 0.0, 1.0))
        is_live = (texture_score >= 0.55) and not is_moire and not is_specular and (skin_fraction >= 0.08)

        spoof_reason = None
        if is_moire:
            spoof_reason = "SCREEN_MOIRE"
        elif is_specular:
            spoof_reason = "SPECULAR_GLARE"
        elif skin_fraction < 0.08:
            spoof_reason = "UNNATURAL_COLOR"

        details = f"Texture: score={texture_score:.2f} (skin={skin_fraction*100:.0f}%, peak/mean={peak_to_mean_ratio:.1f})"

        return TextureLivenessResult(
            is_live=is_live,
            score=texture_score,
            moire_detected=is_moire,
            specular_glare_detected=is_specular,
            spoof_reason=spoof_reason,
            details=details
        )
