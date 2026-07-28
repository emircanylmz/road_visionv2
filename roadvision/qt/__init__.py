"""RoadVision PyQt6 arayüzü (v2 tasarımı).

Bu paket yalnız Qt'ye bağımlıdır; motor, günlük ve arşiv katmanları
``roadvision.ui`` ile aynı sözleşmeler üzerinden kullanılır. Torch/cv2
gerektiren modüller pencere kurulurken tembel yüklenir.
"""

from __future__ import annotations

__all__ = ["run_app"]


def run_app() -> None:
    from .main_window import run_app as _run_app

    _run_app()
