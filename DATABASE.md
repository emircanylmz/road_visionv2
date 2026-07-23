# RoadVision PostgreSQL günlük ve tespit görüntüsü kaydı

Günlük hattının üçüncü sink'i: kayıtlar JSONL dosyası ve oturum ekranına ek olarak PostgreSQL'e yazılabilir. Mimari değişmedi — `PostgresSink`, `LogSink` sözleşmesini uygulayan sıradan bir sink'tir ve `create_default_journal` tarafından `ROADVISION_DB_DSN` ortam değişkeni doluysa otomatik eklenir.

Tespit görüntüleri ayrı bir `MediaRecorder` hattından, inference'ı ağ/JPEG
I/O'sunda bekletmeden yazılır. Aynı DSN kullanılır; görüntü kaydı
`ROADVISION_MEDIA=off` ile bağımsız olarak kapatılabilir.

## Kurulum

### Docker ile yerel PostgreSQL

Proje, PostgreSQL 17 resmi imajını yalnız `127.0.0.1:5433` üzerinde
yayınlayan bir Compose servisi içerir. `5432` makinedeki başka bir
PostgreSQL kurulumu tarafından kullanılabildiği için varsayılan Docker
portu `5433` seçilmiştir.

```bash
cp .env.example .env
docker compose up -d
docker compose ps
python3 -m pip install -r requirements-db.txt
./scripts/run_with_db.sh
```

İlk volume oluşturulurken `db/schema.sql` otomatik uygulanır. RoadVision
da sürüm-kapılı, transaction/advisory-lock korumalı migration kontrolünü
her yeni bağlantıda çalıştırır. Veriler
`roadvision_postgres_data` adlı Docker volume'ünde kalıcıdır.

Container'ı durdurmak için:

```bash
docker compose down
```

### Harici PostgreSQL

```bash
pip install "psycopg[binary]>=3.1,<4"
export ROADVISION_DB_DSN="postgresql://kullanici:parola@sunucu:5432/roadvision"
python3 app.py
```

Şema ilk bağlantıda otomatik uygulanır (`CREATE TABLE IF NOT EXISTS`); elle kurmak isterseniz `db/schema.sql` dosyasını `psql` ile çalıştırın. psycopg kurulu değilse veya DSN hatalıysa uygulama uyarı basıp DB'siz devam eder — JSONL kaydı her koşulda sürer.

## Günlük veri modeli

Üç tablo, iki katman:

**`log_records` — her şey, olduğu gibi.** app + detection tüm kayıtlar LogRecord alanlarıyla birebir buraya yazılır (`ts TIMESTAMPTZ`, `level`, `category`, `message`, `run_id`, `model_id`, `payload JSONB`, `ingest_key`). Ham arşiv budur.

**`detection_events` — kare/model başına tespit olayı.** Tekrar bastırmadan geçen her tespit kaydı (`changed`, mekânsal görüntü yakalamasında `capture`, `heartbeat`, seri kapanışları) bir satırdır: `object_count`, `elapsed_ms`, `dedup`, `repeated_frames`.
Görüntü alınan değişim olaylarında `capture_id`, medya tablolarıyla
korelasyonu taşır; görüntü kuyruğu doluysa veya kapı reddederse NULL'dır.

**`detected_objects` — tekil tespitler, türe göre sorgulanabilir.** Olaydaki her nesne ayrı satır: **`class_name` (tür), `confidence` (doğruluk), `ts` (tarih-saat)**, `bbox REAL[]` (xyxy piksel), semantic için `area_ratio`. Semantic maskede güven skoru olmadığından `confidence` NULL'dır. Tür ve model bazlı indeksler hazır:

```sql
-- Türe göre son 24 saatin tespitleri ve ortalama doğruluk
SELECT class_name, count(*) AS adet, round(avg(confidence)::numeric, 3) AS ort_dogruluk
FROM detected_objects
WHERE ts > now() - interval '24 hours'
GROUP BY class_name ORDER BY adet DESC;

-- Belirli türün zaman içindeki dağılımı
SELECT date_trunc('hour', ts) AS saat, count(*)
FROM detected_objects WHERE class_name = 'pothole'
GROUP BY 1 ORDER BY 1;
```

`detected_objects.ts/run_id/model_id` bilinçli olarak denormalize edildi: türe göre analitik sorgular join'siz çalışır. Hacim büyürse (`ts` üzerinden aylık) partitioning eklenebilir; şema buna hazırdır.

## Görüntü veri modeli

- `media_blobs`: JPEG baytları, boyut ve `sha256`. İçerik-adreslidir; aynı
  JPEG yalnız bir kez saklanır.
- `media_captures`: fiziksel kare başına tek satır ve ham/işaretli blob
  kimlikleri. Buradaki “ham/original”, kaynak dosyanın birebir baytları
  değil, modele giren yönü düzeltilmiş karenin en çok 1280 px olacak şekilde
  küçültülmüş JPEG temsilidir.
- `media_capture_models`: bu kareyi tetikleyen modeller ve mekânsal imzaları.
  Aynı karede iki model tetiklerse tek capture ve en çok iki blob, iki model
  ilişki satırı oluşur. İşaretli blob tüm seçili modellerin ortak canvas'ıdır.

Son görüntüleri ve tetikleyen modelleri görmek:

```sql
SELECT c.capture_id, c.ts, c.source_name,
       string_agg(m.model_id, ', ' ORDER BY m.model_id) AS modeller,
       original.byte_size AS ham_boyut,
       annotated.byte_size AS isaretli_boyut
FROM media_captures c
JOIN media_capture_models m USING (capture_id)
JOIN media_blobs original ON original.id = c.original_media_id
JOIN media_blobs annotated ON annotated.id = c.annotated_media_id
GROUP BY c.capture_id, c.ts, c.source_name,
         original.byte_size, annotated.byte_size
ORDER BY c.ts DESC
LIMIT 50;
```

Olay ile görüntüyü eşleştirmek:

```sql
SELECT e.ts, e.model_id, e.object_count, c.capture_id,
       c.source_name, c.frame_sequence
FROM detection_events e
LEFT JOIN media_captures c USING (capture_id)
WHERE e.ts > now() - interval '1 day'
ORDER BY e.ts DESC;
```

pgAdmin veri ızgarası `BYTEA` alanını hex/binary olarak gösterir; JPEG'i
görsel önizleme olarak açmaz. Capture kimliğini yukarıdaki sorgudan alıp
iki dosyayı şöyle dışa aktarın:

```bash
set -a; . ./.env; set +a
python3 scripts/export_media.py CAPTURE_UUID --output-dir exports
```

## Güvenilirlik davranışı

- Journal'ın yazıcı thread'i asla ağ beklemez: `write_record` sınırlı iç kuyruğa bırakır (5000 kayıt), ayrı flusher thread toplu yazar (100'lük batch / 2 sn).
- Veritabanı kapalıysa ilk bağlantı/yazma hatası stderr'e bildirilir, aynı kesinti boyunca tekrarlar bastırılır ve 1→30 sn üstel geri çekilme ile yeniden denenir. Başarılı bir yazımdan sonraki yeni kesinti yeniden raporlanır.
- Her canlı kayıt oluşturulurken benzersiz bir `ingest_key` alır; aynı anahtar JSONL satırında saklanır ve PostgreSQL retry boyunca korunur. Böylece hem commit sonucunun belirsiz kaldığı bağlantı kopmalarında hem de JSONL backfill işleminde mükerrer satır oluşmaz.
- Başarısız batch kuyruğun önüne geri konur. Veri kaybı yalnız kuyruk taşarsa olur; o durumda en eski kayıt atılır ve atılan sayısı, yazma ilk denemede başarısız olsa bile korunan bir `db_dropped` uyarısıyla veritabanına düşülür.
- Uygulama kapanırken `release_sink`, veritabanı erişilebildiği ölçüde kalan kayıtları yazar ve bağlantıyı kapatır; JSONL kaydı backfill için kalır.
- Günlük ve medya kuyrukları bilinçli olarak sınırlıdır. Aşırı yükte veya
  uzun DB kesintisinde kayıt düşebilir ve uyarı üretilir; bu hatlar
  best-effort'tur, kalıcı transactional outbox garantisi vermez.

## Geçmiş JSONL dosyalarını aktarma (backfill)

```bash
python3 scripts/backfill_jsonl.py ~/.cache/roadvision/logs/roadvision.jsonl
```

Script idempotenttir: yeni satırlarda JSONL içindeki canlı `ingest_key`, eski anahtarsız satırlarda dosya+satır+içerikten türetilen anahtar kullanılır. Tekrar çalıştırmak veya canlı yazılmış kayıtları sonradan backfill etmek yinelenen kayıt üretmez (`ON CONFLICT DO NOTHING`; olay atlanınca bağlı nesneler de atlanır). Çıktı yalnız gerçekten yeni eklenen kayıtları sayar; bozuk satırlar raporlanıp geçilir.

## Tespit verisinin kaynağı

Bu sürümle boru hattı tekil tespit taşır: `extract_objects` (saf modül, ultralytics import etmez) YOLO sonucundan sınıf adı + güven + bbox çıkarır, `ModelRunStat.objects` ile journal'a ulaşır. Tekrar bastırma imzası da sınıf-başına sayıma yükseldi: toplam sayı aynı kalsa bile tür bileşimi değişirse (2 çukur → 1 çukur + 1 rögar) yeni kayıt yazılır.

Medya kapısı journal imzasından bilinçli olarak daha zengindir: sınıfın
yanında normalize ve kuantize bbox/semantic footprint kullanır. Aynı sayıda
nesne sahnede belirgin hareket ederse yeni görüntü alınabilir; birkaç
piksellik titreşim bastırılır. Minimum aralık ve kota fiziksel kare başına
uygulanır.

## Saklama ve boyut kotası

Her başarılı medya yazımından sonra 30 günlük süre ve yaklaşık 2048 MB
`SUM(media_blobs.byte_size)` kotası otomatik uygulanır. Bu değer PostgreSQL
TOAST/indeks/WAL/yedek overhead'ini içermez; fiziksel disk boyutu `DELETE`
sonrası VACUUM alanı yeniden kullanana dek hemen küçülmeyebilir.

Manuel kontrol varsayılan olarak dry-run'dır:

```bash
set -a; . ./.env; set +a
python3 scripts/prune_media.py
python3 scripts/prune_media.py --apply
```

Capture grubu atomik silinir; ardından yalnız hiçbir ham/işaretli FK'si
kalmayan blob'lar temizlenir. Ayarlar `.env.example` içindeki
`ROADVISION_MEDIA_*` değişkenleriyle değiştirilebilir.

## Sınırlar / bilinçli kararlar

- DB'ye giden tespitler bastırmadan **geçen** olaylardır (JSONL ile aynı). Her karenin her nesnesi istenirse `DetectionSuppressor(heartbeat_seconds=...)` düşürülerek yoğunluk artırılabilir; 30 FPS × N nesne ham akışı saklamak bilinçli olarak varsayılan değildir.
- Yeni uygulama kayıtlarında JSONL ve PostgreSQL arasında paylaşılan rastgele ve sabit bir `ingest_key`, yalnız eski anahtarsız JSONL kayıtlarında deterministik backfill anahtarı kullanılır.
- Bağlantı tek'tir (pool yok); tek uygulama örneği için yeterlidir.
