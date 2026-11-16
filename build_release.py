"""
Peek Face Authentication - Release Build Wrapper
Usage:
    python build_release.py [--skip-tests] [--clean] [--version 0.2.0-beta.1]
"""

import os
import sys
import subprocess

def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    ps_script = os.path.join(repo_root, "scripts", "release", "build_release.ps1")

    if not os.path.isfile(ps_script):
        print(f"Error: Could not locate build script at {ps_script}", file=sys.stderr)
        sys.exit(1)

    ps_args = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ps_script
    ]

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ("--skip-tests", "-SkipTests", "-skip-tests", "--skip_tests"):
            ps_args.append("-SkipTests")
        elif arg in ("--clean", "-Clean", "-clean"):
            ps_args.append("-Clean")
        elif arg in ("--version", "-Version", "-version", "-v"):
            if i + 1 < len(sys.argv):
                i += 1
                ps_args.extend(["-Version", sys.argv[i]])
            else:
                print("Error: --version requires a version string.", file=sys.stderr)
                sys.exit(1)
        else:
            ps_args.append(arg)
        i += 1

    try:
        proc = subprocess.run(ps_args, cwd=repo_root)
        sys.exit(proc.returncode)
    except FileNotFoundError:
        print("Error: powershell.exe could not be found in PATH.", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
