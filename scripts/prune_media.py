#!/usr/bin/env python3
"""RoadVision görüntülerine süre ve yaklaşık blob-boyutu kotası uygular.

Varsayılan çalışma dry-run'dır; gerçekten silmek için ``--apply`` gerekir.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roadvision.config import MediaConfig  # noqa: E402
from roadvision.db import (  # noqa: E402
    default_connection_factory,
    ensure_schema,
    prune_media,
)


def _mb(value: int) -> str:
    return f"{value / (1024 * 1024):.1f} MB"


def main() -> int:
    try:
        defaults = MediaConfig.from_env()
    except ValueError as exc:
        print(f"Geçersiz medya ortam ayarı: {exc}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("ROADVISION_DB_DSN"),
        help=(
            "PostgreSQL DSN. Parola içeren DSN'i komut satırında vermek onu süreç listesinde ve kabuk geçmişinde görünür kılar; tercihen ROADVISION_DB_DSN ortam değişkenini kullanın."
        ),
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=defaults.retention_days,
        help=f"Saklama süresi (varsayılan: {defaults.retention_days})",
    )
    parser.add_argument(
        "--max-total-mb",
        type=int,
        default=defaults.max_total_mb,
        help=f"Blob toplamı hedefi (varsayılan: {defaults.max_total_mb})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Silme transaction'ını commit et (verilmezse rollback/dry-run)",
    )
    args = parser.parse_args()
    if not args.dsn:
        parser.error("DSN verilmedi: --dsn veya ROADVISION_DB_DSN kullanın.")
    if args.retention_days <= 0 or args.max_total_mb <= 0:
        parser.error("retention-days ve max-total-mb pozitif olmalıdır.")

    conn = default_connection_factory(args.dsn)
    try:
        ensure_schema(conn)
        result = prune_media(
            conn,
            retention_days=args.retention_days,
            max_total_bytes=args.max_total_mb * 1024 * 1024,
            commit=args.apply,
        )
        if not args.apply:
            conn.rollback()
        mode = "UYGULANDI" if args.apply else "DRY-RUN (geri alındı)"
        print(
            f"{mode}: {result.captures_deleted} capture, "
            f"{result.blobs_deleted} blob; "
            f"{_mb(result.bytes_before)} → {_mb(result.bytes_after)}"
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
