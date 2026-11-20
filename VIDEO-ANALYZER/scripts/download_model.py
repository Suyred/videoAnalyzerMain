"""Download and verify the YOLOv8n ONNX model used by the service."""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import config  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    target = config.MODEL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 1_000_000:
        if not config.MODEL_SHA256 or sha256(target).lower() == config.MODEL_SHA256.lower():
            print(f"Model already available and checksum accepted: {target}")
            return
        target.unlink()

    temporary = target.with_suffix(target.suffix + ".download")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(
        config.MODEL_URL, headers={"User-Agent": "VIDEO-ANALYZER/2.0"}
    )
    print("Downloading YOLOv8n ONNX model...")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        if temporary.stat().st_size < 1_000_000:
            raise RuntimeError("Downloaded model is unexpectedly small.")
        actual = sha256(temporary)
        if config.MODEL_SHA256 and actual.lower() != config.MODEL_SHA256.lower():
            raise RuntimeError("Model checksum verification failed.")
        temporary.replace(target)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise SystemExit(f"Model download/verification failed: {exc}") from exc
    print(f"Verified model saved to: {target}")


if __name__ == "__main__":
    main()
