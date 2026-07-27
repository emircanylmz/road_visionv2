#!/usr/bin/env python3
"""Bir RoadVision capture'ının ham/işaretli JPEG çiftini diske çıkarır."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roadvision.db import default_connection_factory, ensure_schema  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_id", help="media_captures.capture_id UUID değeri")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("exports"),
        help="Çıktı klasörü (varsayılan: ./exports)",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("ROADVISION_DB_DSN"),
        help=(
            "PostgreSQL DSN. Parola içeren DSN'i komut satırında vermek onu süreç listesinde ve kabuk geçmişinde görünür kılar; tercihen ROADVISION_DB_DSN ortam değişkenini kullanın."
        ),
    )
    args = parser.parse_args()
    if not args.dsn:
        parser.error("DSN verilmedi: --dsn veya ROADVISION_DB_DSN kullanın.")

    conn = default_connection_factory(args.dsn)
    try:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT original.data, annotated.data
                FROM media_captures capture
                JOIN media_blobs original ON original.id = capture.original_media_id
                JOIN media_blobs annotated ON annotated.id = capture.annotated_media_id
                WHERE capture.capture_id = %s
                """,
                (args.capture_id,),
            )
            row = cur.fetchone()
        if row is None:
            print(f"Capture bulunamadı: {args.capture_id}", file=sys.stderr)
            return 1
        args.output_dir.mkdir(parents=True, exist_ok=True)
        original_path = args.output_dir / f"{args.capture_id}-original.jpg"
        annotated_path = args.output_dir / f"{args.capture_id}-annotated.jpg"
        original_path.write_bytes(bytes(row[0]))
        annotated_path.write_bytes(bytes(row[1]))
        print(original_path)
        print(annotated_path)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
