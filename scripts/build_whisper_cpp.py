"""Download and compile whisper.cpp (https://github.com/ggml-org/whisper.cpp),
then copy the resulting binaries into <project>/bin.

Usage:
    python scripts/build_whisper_cpp.py [--version vX.Y.Z] [extra cmake args...]

Examples:
    python scripts/build_whisper_cpp.py                     # CPU build
    python scripts/build_whisper_cpp.py -DGGML_VULKAN=ON    # Vulkan GPU build
    python scripts/build_whisper_cpp.py -DGGML_CUDA=ON      # CUDA GPU build
    python scripts/build_whisper_cpp.py --version v1.9.5

Requires: git, cmake, a C/C++ compiler (gcc/clang, or MSVC on Windows).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/ggml-org/whisper.cpp"
PROJECT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_DIR / "build" / "whisper.cpp"
BUILD_DIR = SRC_DIR / "build"
BIN_DIR = PROJECT_DIR / "bin"


def run(*cmd: str | Path) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def latest_version() -> str:
    out = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", REPO_URL, "v*"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    versions = []
    for line in out.splitlines():
        m = re.search(r"refs/tags/(v(\d+)\.(\d+)\.(\d+))$", line)
        if m:
            versions.append((tuple(map(int, m.groups()[1:])), m.group(1)))
    if not versions:
        sys.exit("error: could not determine latest whisper.cpp version")
    return max(versions)[1]


def checkout(version: str) -> None:
    if (SRC_DIR / ".git").is_dir():
        run("git", "-C", SRC_DIR, "fetch", "--depth", "1", "origin", "tag", version)
        run("git", "-C", SRC_DIR, "checkout", "--force", version)
    else:
        if SRC_DIR.exists():
            shutil.rmtree(SRC_DIR)
        run("git", "clone", "--depth", "1", "--branch", version, REPO_URL, SRC_DIR)


def build(cmake_args: list[str]) -> None:
    # Static libraries so the copied binaries don't depend on the build tree.
    run(
        "cmake",
        "-S",
        SRC_DIR,
        "-B",
        BUILD_DIR,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=OFF",
        "-DWHISPER_BUILD_TESTS=OFF",
        *cmake_args,
    )
    run(
        "cmake",
        "--build",
        BUILD_DIR,
        "--config",
        "Release",
        "-j",
        str(os.cpu_count() or 4),
    )


def is_binary(path: Path) -> bool:
    if not path.is_file():
        return False
    if sys.platform == "win32":
        return path.suffix.lower() in (".exe", ".dll")
    return os.access(path, os.X_OK)


def copy_binaries() -> list[str]:
    # Single-config generators (Make/Ninja) put binaries in build/bin;
    # multi-config generators (Visual Studio) in build/bin/Release.
    out_dir = BUILD_DIR / "bin"
    if (out_dir / "Release").is_dir():
        out_dir = out_dir / "Release"

    BIN_DIR.mkdir(exist_ok=True)
    copied = []
    for path in sorted(out_dir.iterdir()):
        if is_binary(path):
            shutil.copy2(path, BIN_DIR / path.name)
            copied.append(path.name)
    if not copied:
        sys.exit(f"error: no binaries found in {out_dir}")
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--version", help="whisper.cpp release tag (default: latest release)"
    )
    args, cmake_args = parser.parse_known_args()

    for tool in ("git", "cmake"):
        if shutil.which(tool) is None:
            sys.exit(f"error: '{tool}' not found on PATH")

    version = args.version or latest_version()
    print(f"==> whisper.cpp {version}", flush=True)

    checkout(version)
    build(cmake_args)
    copied = copy_binaries()

    print(f"==> Binaries copied to {BIN_DIR}:")
    for name in copied:
        print(f"  {name}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"error: command failed with exit code {e.returncode}")
