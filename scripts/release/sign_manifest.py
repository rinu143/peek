"""
Python wrapper for scripts/release/sign_manifest.ps1.
Usage:
    python sign_manifest.py [args]
"""

import os
import sys
import subprocess

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(script_dir))
    ps_script = os.path.join(script_dir, "sign_manifest.ps1")

    ps_args = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ps_script
    ] + sys.argv[1:]

    proc = subprocess.run(ps_args, cwd=repo_root)
    sys.exit(proc.returncode)

if __name__ == "__main__":
    main()
