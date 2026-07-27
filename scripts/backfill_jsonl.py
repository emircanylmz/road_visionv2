#!/usr/bin/env python3
"""Mevcut JSONL günlük dosyalarını PostgreSQL'e aktarır.

Kullanım:
    ROADVISION_DB_DSN="postgresql://user:pass@host/dbname" \
        python3 scripts/backfill_jsonl.py ~/.cache/roadvision/logs/roadvision.jsonl [...]

Script idempotenttir: yeni JSONL satırlarında kayıtla taşınan ingest_key'i,
eski satırlarda dosya adı + satır numarası + içerikten türetilen anahtarı
kullanır. `ON CONFLICT DO NOTHING` tekrar çalıştırıldığında yinelenen kayıt
üretmez. Bozuk satırlar atlanır ve raporlanır.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roadvision.db import (  # noqa: E402
    default_connection_factory,
    ensure_schema,
    ingest_key_for,
    record_from_json_line,
    write_batch,
)

BATCH_SIZE = 500


def backfill(path: Path, conn) -> tuple[int, int]:
    imported = skipped = 0
    batch = []
    with path.open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            line = line.strip()
            if not line:
                continue
            record = record_from_json_line(line)
            if record is None:
                skipped += 1
                continue
            ingest_key = record.ingest_key or ingest_key_for(path.name, line_no, line)
            batch.append((record, ingest_key))
            if len(batch) >= BATCH_SIZE:
                imported += write_batch(conn, batch)
                batch = []
    if batch:
        imported += write_batch(conn, batch)
    return imported, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="JSONL günlük dosyaları")
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
        for path in args.files:
            if not path.is_file():
                print(f"atlandı (dosya yok): {path}", file=sys.stderr)
                continue
            imported, skipped = backfill(path, conn)
            print(f"{path}: {imported} kayıt aktarıldı, {skipped} bozuk satır atlandı")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
