"""
Peek Secure Profile Store
Layer E: DPAPI-encrypted biometric profile storage for face templates.

SECURITY CONSTRAINTS:
1. No raw camera frames or pixel data are ever stored.
2. Embeddings and profile metadata are encrypted at rest using Windows DPAPI (CryptProtectData).
3. Versioned schema with migration hooks.
"""

import os
import json
import time
import uuid
import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

logger = logging.getLogger("Peek.Storage")

# Windows DPAPI Setup
class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_byte))
    ]

crypt32 = ctypes.windll.crypt32
kernel32 = ctypes.windll.kernel32

def dpapi_encrypt(data: bytes, description: str = "PeekBiometricData") -> bytes:
    """Encrypts bytes using Windows DPAPI (CryptProtectData)."""
    in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), description, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)

def dpapi_decrypt(ciphertext: bytes) -> bytes:
    """Decrypts bytes using Windows DPAPI (CryptUnprotectData)."""
    in_blob = DATA_BLOB(len(ciphertext), ctypes.cast(ctypes.create_string_buffer(ciphertext), ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


@dataclass
class PeekTemplate:
    template_id: str
    embedding: List[float]  # 512-D float list
    pose_label: str  # "CENTER", "LEFT", "RIGHT", etc.
    quality_score: float
    captured_at: float = field(default_factory=time.time)


@dataclass
class PeekProfile:
    profile_id: str
    display_name: str
    created_at: float = field(default_factory=time.time)
    enabled: bool = True
    matching_threshold: float = 0.50
    templates: List[PeekTemplate] = field(default_factory=list)
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "created_at": self.created_at,
            "enabled": self.enabled,
            "matching_threshold": self.matching_threshold,
            "templates": [asdict(t) for t in self.templates]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PeekProfile":
        templates = [PeekTemplate(**t) for t in data.get("templates", [])]
        return cls(
            schema_version=data.get("schema_version", 1),
            profile_id=data["profile_id"],
            display_name=data["display_name"],
            created_at=data.get("created_at", time.time()),
            enabled=data.get("enabled", True),
            matching_threshold=data.get("matching_threshold", 0.50),
            templates=templates
        )


class SecureProfileStore:
    """
    Manages loading, saving, and querying encrypted biometric profiles.
    All data persisted to disk is encrypted via Windows DPAPI.
    """

    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir is None:
            # Default to %LOCALAPPDATA%\Peek\Profiles
            base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
            self.storage_dir = os.path.join(base, "Peek", "Profiles")
        else:
            self.storage_dir = storage_dir

        os.makedirs(self.storage_dir, exist_ok=True)

    def _get_profile_path(self, profile_id: str) -> str:
        return os.path.join(self.storage_dir, f"{profile_id}.peek")

    def save_profile(self, profile: PeekProfile) -> str:
        """
        Encrypts and saves a PeekProfile to disk using DPAPI.
        Returns the file path.
        """
        raw_json = json.dumps(profile.to_dict()).encode("utf-8")
        encrypted_data = dpapi_encrypt(raw_json, description=f"PeekProfile_{profile.profile_id}")
        
        file_path = self._get_profile_path(profile.profile_id)
        with open(file_path, "wb") as f:
            f.write(encrypted_data)

        logger.info(f"Saved DPAPI-encrypted profile '{profile.display_name}' ({profile.profile_id}) to {file_path}")
        return file_path

    def load_profile(self, profile_id: str) -> Optional[PeekProfile]:
        """
        Loads and decrypts a PeekProfile from disk using DPAPI.
        """
        file_path = self._get_profile_path(profile_id)
        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, "rb") as f:
                encrypted_data = f.read()
            raw_json = dpapi_decrypt(encrypted_data).decode("utf-8")
            data = json.loads(raw_json)
            return PeekProfile.from_dict(data)
        except Exception as ex:
            logger.error(f"Failed to load/decrypt profile {profile_id}: {ex}")
            return None

    def list_profiles(self) -> List[PeekProfile]:
        """Lists all decrypted profiles currently on disk."""
        profiles = []
        if not os.path.exists(self.storage_dir):
            return profiles

        for fname in os.listdir(self.storage_dir):
            if fname.endswith(".peek"):
                pid = fname[:-5]
                p = self.load_profile(pid)
                if p is not None:
                    profiles.append(p)
        return profiles

    def delete_profile(self, profile_id: str) -> bool:
        """Deletes an encrypted profile from disk."""
        file_path = self._get_profile_path(profile_id)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Deleted profile {profile_id}")
            return True
        return False
