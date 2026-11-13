"""Download trusted portable FFmpeg build and deploy to tools/ffmpeg/bin/"""
import os
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_BIN = ROOT / "tools" / "ffmpeg" / "bin"
CACHE_DIR = ROOT / "cache" / "downloads"
TOOLS_BIN.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
ZIP_PATH = CACHE_DIR / "ffmpeg-win64.zip"


def download_with_progress(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 10_000_000:
        print(f"Archive already cached at {dest} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
        return
    print(f"Downloading FFmpeg from {url}...")
    headers = {"User-Agent": "ClipForge-Setup"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        t0 = time.time()
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            pct = (downloaded / total * 100) if total else 0
            mb = downloaded / (1024 * 1024)
            speed = mb / max(0.01, time.time() - t0)
            print(f"\rDownloaded {mb:.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s", end="", flush=True)
    print("\nDownload complete!")


def extract_binaries(zip_path: Path, target_dir: Path) -> None:
    print(f"Extracting ffmpeg.exe and ffprobe.exe from {zip_path} into {target_dir}...")
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            name = Path(member).name
            if name in ("ffmpeg.exe", "ffprobe.exe"):
                print(f"Extracting {name}...")
                with z.open(member) as src, open(target_dir / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    print("Extraction finished!")


def main():
    download_with_progress(FFMPEG_URL, ZIP_PATH)
    extract_binaries(ZIP_PATH, TOOLS_BIN)
    ffmpeg_exe = TOOLS_BIN / "ffmpeg.exe"
    ffprobe_exe = TOOLS_BIN / "ffprobe.exe"
    if ffmpeg_exe.exists() and ffprobe_exe.exists():
        print(f"SUCCESS: Deployed ffmpeg to {ffmpeg_exe} ({ffmpeg_exe.stat().st_size / 1024 / 1024:.1f} MB)")
        print(f"SUCCESS: Deployed ffprobe to {ffprobe_exe} ({ffprobe_exe.stat().st_size / 1024 / 1024:.1f} MB)")
    else:
        print("ERROR: Binaries missing after extraction!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

