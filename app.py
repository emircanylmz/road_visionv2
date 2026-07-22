#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


cache_dir = Path(__file__).resolve().parent / ".cache" / "matplotlib"
cache_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
# torchvision::nms henüz Apple MPS üzerinde uygulanmadı. Modelin geri kalanı
# MPS'te çalışırken desteklenmeyen operatörlerin CPU'ya düşmesine izin ver.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from roadvision.ui import run_app  # noqa: E402


if __name__ == "__main__":
    run_app()
