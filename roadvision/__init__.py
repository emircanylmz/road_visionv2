"""RoadVision multi-model test application."""

import os

# Bu ayar torch ilk kez import edilmeden önce yapılmalıdır. app.py dışından
# paket doğrudan kullanıldığında da MPS NMS fallback'inin aktif kalmasını sağlar.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from .config import APP_CONFIG, MODEL_SPECS

__all__ = ["APP_CONFIG", "MODEL_SPECS"]
