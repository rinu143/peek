#!/usr/bin/env python3
"""
Generates build/peek.ico from credential_provider/res/frame_search_1_dark.png
using Pillow (PIL), bundling sizes 16, 24, 32, 48, 64, 128, and 256.
"""

import os
import sys

def make_icon():
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is missing; cannot generate peek.ico. Continuing without custom icon.")
        sys.exit(0)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    src_png = os.path.join(repo_root, "credential_provider", "res", "frame_search_1_dark.png")
    out_dir = os.path.join(repo_root, "build")
    out_ico = os.path.join(out_dir, "peek.ico")

    if not os.path.exists(src_png):
        print(f"Error: Source PNG not found: {src_png}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)

    img = Image.open(src_png).convert("RGBA")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(out_ico, format="ICO", sizes=sizes)
    print(f"Generated {out_ico} with sizes: {sizes}")

if __name__ == "__main__":
    make_icon()
