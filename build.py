#!/usr/bin/env python3
"""Build a standalone executable for portfolio_visualizer.py using PyInstaller.

Usage:
  python build.py
  python build.py --onefile
  python build.py --name portfolio-visualizer --clean
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_SCRIPT = ROOT / "portfolio_visualizer.py"
DEFAULT_NAME = "portfolio_visualizer"


def ensure_pyinstaller() -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller is not installed. Installing with pip...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def run_build(name: str, onefile: bool, clean: bool, noconsole: bool) -> int:
    if not APP_SCRIPT.exists():
        print(f"Error: missing app script: {APP_SCRIPT}")
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        name,
    ]

    if clean:
        cmd.append("--clean")
    if onefile:
        cmd.append("--onefile")
    if noconsole:
        cmd.append("--windowed")

    cmd.append(str(APP_SCRIPT))

    print("Running:")
    print(" ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


def cleanup_build_artifacts(name: str) -> None:
    build_dir = ROOT / "build"
    spec_file = ROOT / f"{name}.spec"

    if build_dir.exists():
        shutil.rmtree(build_dir)
    if spec_file.exists():
        spec_file.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build executable for portfolio_visualizer.py")
    parser.add_argument("--name", default=DEFAULT_NAME, help="Executable name (default: portfolio_visualizer)")
    parser.add_argument("--onefile", action="store_true", help="Build a single-file executable")
    parser.add_argument("--clean", action="store_true", help="Run clean PyInstaller build")
    parser.add_argument(
        "--console",
        action="store_true",
        help="Show console window (default is GUI mode with no console)",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove build folder and .spec file after successful build",
    )
    args = parser.parse_args()

    ensure_pyinstaller()

    code = run_build(
        name=args.name,
        onefile=args.onefile,
        clean=args.clean,
        noconsole=not args.console,
    )
    if code != 0:
        print("Build failed.")
        return code

    if args.onefile:
        exe_path = ROOT / "dist" / args.name
    else:
        exe_path = ROOT / "dist" / args.name / args.name

    print(f"Build succeeded. Executable: {exe_path}")

    if args.cleanup:
        cleanup_build_artifacts(args.name)
        print("Removed temporary build artifacts (build/ and .spec).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
