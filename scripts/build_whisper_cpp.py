"""Download and compile whisper.cpp (https://github.com/ggml-org/whisper.cpp),
then copy the resulting binaries into <project>/bin.

Usage:
    python scripts/build_whisper_cpp.py [--version vX.Y.Z] [--vulkan] [extra cmake args...]

Examples:
    python scripts/build_whisper_cpp.py                     # CPU build
    python scripts/build_whisper_cpp.py --vulkan            # Vulkan GPU build
    python scripts/build_whisper_cpp.py -DGGML_CUDA=ON      # CUDA GPU build
    python scripts/build_whisper_cpp.py --version v1.9.5

Requires: git, cmake, a C/C++ compiler (gcc/clang, or MSVC on Windows).
--vulkan additionally needs the Vulkan headers/loader, the glslc shader
compiler and SPIRV-Headers. On apt-based Linux the script checks for
libvulkan-dev, glslc and spirv-headers and offers to install missing ones;
elsewhere install them yourself (Windows: the Vulkan SDK).
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

# Debian/Ubuntu packages needed by ggml's Vulkan backend: Vulkan headers and
# loader, the glslc shader compiler and SPIRV-Headers (CMake config).
APT_VULKAN_PACKAGES = ("libvulkan-dev", "glslc", "spirv-headers")


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


def missing_apt_packages(packages: tuple[str, ...]) -> list[str]:
    missing = []
    for pkg in packages:
        status = subprocess.run(
            ["dpkg-query", "-W", "-f=${db:Status-Abbrev}", pkg],
            capture_output=True,
            text=True,
        ).stdout
        if not status.startswith("ii"):
            missing.append(pkg)
    return missing


def ensure_vulkan_deps() -> None:
    """On apt-based Linux, offer to install missing Vulkan build packages."""
    if (
        sys.platform.startswith("linux")
        and shutil.which("apt-get")
        and shutil.which("dpkg-query")
    ):
        missing = missing_apt_packages(APT_VULKAN_PACKAGES)
        if missing:
            cmd = ["apt-get", "install", *missing]
            if os.geteuid() != 0:
                cmd.insert(0, "sudo")
            print(f"Missing packages for the Vulkan build: {' '.join(missing)}")
            if not sys.stdin.isatty():
                sys.exit(f"error: install them with: {' '.join(cmd)}")
            answer = input(f"Install them now with '{' '.join(cmd)}'? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                sys.exit(f"error: missing packages: {' '.join(missing)}")
            run(*cmd)
    if shutil.which("glslc") is None:
        sys.exit("error: 'glslc' not found on PATH (install the Vulkan SDK)")


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
            # Copy then rename: overwriting a running binary in place fails
            # ("Text file busy"); a rename leaves running processes untouched.
            dest = BIN_DIR / path.name
            tmp = dest.with_name(dest.name + ".tmp")
            shutil.copy2(path, tmp)
            os.replace(tmp, dest)
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
    parser.add_argument(
        "--vulkan",
        action="store_true",
        help="build with Vulkan GPU acceleration",
    )
    args, cmake_args = parser.parse_known_args()

    for tool in ("git", "cmake"):
        if shutil.which(tool) is None:
            sys.exit(f"error: '{tool}' not found on PATH")
    if args.vulkan:
        ensure_vulkan_deps()

    # Always set GGML_VULKAN explicitly: the build dir's CMake cache would
    # otherwise keep the previous build's choice. Extra args come later and win.
    cmake_args = [f"-DGGML_VULKAN={'ON' if args.vulkan else 'OFF'}", *cmake_args]

    version = args.version or latest_version()
    print(f"==> whisper.cpp {version}{' (Vulkan)' if args.vulkan else ''}", flush=True)

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
