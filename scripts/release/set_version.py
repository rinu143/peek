"""
Python wrapper for scripts/release/set_version.ps1.
Usage:
    python set_version.py [0.2.0-beta.1]
"""

import os
import sys
import subprocess

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(script_dir))
    ps_script = os.path.join(script_dir, "set_version.ps1")

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
