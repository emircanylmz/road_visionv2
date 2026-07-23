"""Model katmanı.

Alt modüller tembel yüklenir (PEP 562): `roadvision.models.detections` gibi
saf modüller, torch/ultralytics kurulmadan da import edilebilir; ağır
bağımlılıklar ancak ModelManager gerçekten istendiğinde yüklenir.
"""

from __future__ import annotations

__all__ = ["ModelManager", "ModelRegistry", "YoloModelAdapter"]

_LAZY = {
    "ModelManager": ("roadvision.models.manager", "ModelManager"),
    "ModelRegistry": ("roadvision.models.registry", "ModelRegistry"),
    "YoloModelAdapter": ("roadvision.models.yolo", "YoloModelAdapter"),
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib

        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
