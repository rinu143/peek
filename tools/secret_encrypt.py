#!/usr/bin/env python3
"""
Secret Encryption Script for Peek Credential Provider

This script demonstrates how to use Windows DPAPI to encrypt/decrypt passwords 
for use with the Peek credential provider's secret vault functionality.
"""

import os
import sys
import ctypes
import argparse
from ctypes import wintypes

# Windows DPAPI Setup
class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_byte))
    ]

def get_dpapi_libs():
    """Lazily resolve crypt32/kernel32."""
    try:
        import platform
        if platform.system() != "Windows":
            raise RuntimeError("DPAPI is only available on Windows.")
        
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        return crypt32, kernel32
    except Exception as e:
        print(f"Failed to load DPAPI libraries: {e}")
        sys.exit(1)

def dpapi_encrypt(data: bytes, description: str = "PeekSecret") -> bytes:
    """Encrypts bytes using Windows DPAPI (CryptProtectData)."""
    crypt32, kernel32 = get_dpapi_libs()
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
    crypt32, kernel32 = get_dpapi_libs()
    in_blob = DATA_BLOB(len(ciphertext), ctypes.cast(ctypes.create_string_buffer(ciphertext), ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
        
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)

def main():
    parser = argparse.ArgumentParser(description="Encrypt/decrypt secrets using Windows DPAPI")
    parser.add_argument("action", choices=["encrypt", "decrypt"], help="Action to perform")
    parser.add_argument("--input", "-i", required=True, help="Input file path")
    parser.add_argument("--output", "-o", help="Output file path (for encrypt action)")
    parser.add_argument("--password", "-p", help="Password to encrypt (for encrypt action)")
    
    args = parser.parse_args()
    
    if args.action == "encrypt":
        if not args.password:
            print("Error: --password is required for encryption")
            sys.exit(1)
            
        password_bytes = args.password.encode('utf-16-le')  # Windows passwords are UTF-16
        encrypted_data = dpapi_encrypt(password_bytes, "PeekCredentialSecret")
        
        if args.output:
            with open(args.output, 'wb') as f:
                f.write(encrypted_data)
            print(f"Encrypted data written to {args.output}")
        else:
            # Print the base64 encoded result
            import base64
            encoded = base64.b64encode(encrypted_data).decode('utf-8')
            print(f"Encrypted secret: {encoded}")
            
    elif args.action == "decrypt":
        if not os.path.exists(args.input):
            print(f"Error: Input file {args.input} does not exist")
            sys.exit(1)
            
        with open(args.input, 'rb') as f:
            encrypted_data = f.read()
            
        try:
            decrypted_data = dpapi_decrypt(encrypted_data)
            password = decrypted_data.decode('utf-16-le')
            print(f"Decrypted password: {password}")
        except Exception as e:
            print(f"Failed to decrypt: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()