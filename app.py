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

def _select_run_app():
    """ROADVISION_UI=qt|tk seçer; PyQt6 yoksa Tk'ye geri düşer."""

    choice = os.environ.get("ROADVISION_UI", "qt").strip().lower()
    if choice == "tk":
        from roadvision.ui import run_app

        return run_app
    try:
        import PyQt6  # noqa: F401
    except ImportError:
        import sys

        print(
            "PyQt6 kurulu değil; Tk arayüzüne geri dönülüyor "
            "(pip install PyQt6).",
            file=sys.stderr,
        )
        from roadvision.ui import run_app

        return run_app
    from roadvision.qt import run_app

    return run_app


if __name__ == "__main__":
    _select_run_app()()
