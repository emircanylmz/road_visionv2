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

İlk volume oluşturulurken önce `db/schema.sql`, ardından
`db/roadvision_schema_v1_2_1.sql` otomatik uygulanır. RoadVision da
sürüm-kapılı, transaction/advisory-lock korumalı migration kontrolünü her
yeni bağlantıda çalıştırır. Veriler
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

Şema ilk bağlantıda otomatik uygulanır. Elle temiz kurulum yapmak isterseniz
önce v1/v2 çekirdeğini, sonra v3 migration'ını çalıştırın:

```bash
psql "$ROADVISION_DB_DSN" -v ON_ERROR_STOP=1 -f db/schema.sql
psql "$ROADVISION_DB_DSN" -v ON_ERROR_STOP=1 \
  -f db/roadvision_schema_v1_2_1.sql
```

psycopg kurulu değilse veya DSN hatalıysa uygulama uyarı basıp DB'siz devam
eder — JSONL kaydı her koşulda sürer.

## Günlük veri modeli

Üç tablo, iki katman:

**`log_records` — her şey, olduğu gibi.** app + detection tüm kayıtlar LogRecord alanlarıyla birebir buraya yazılır (`ts TIMESTAMPTZ`, `level`, `category`, `message`, `run_id`, `model_id`, `payload JSONB`, `ingest_key`). Ham arşiv budur.

**`detection_events` — kare/model başına tespit olayı.** Tekrar bastırmadan geçen her tespit kaydı (`changed`, mekânsal görüntü yakalamasında `capture`, `heartbeat`, seri kapanışları) bir satırdır: `object_count`, `elapsed_ms`, `dedup`, `repeated_frames`.
Görüntü alınan değişim olaylarında `capture_id`, medya tablolarıyla
korelasyonu taşır; görüntü kuyruğu doluysa veya kapı reddederse NULL'dır.

**`roadvision_model_catalog` ve `roadvision_detection_type_catalog` —
tanımlı envanter.** Dört aktif modelin kimliği/görevi/girdi boyutu ve
beklenen 20 tespit türü burada tutulur. Bu iki tablo, çalışma zamanında
görülen bilinmeyen türlerden etkilenmeyen referans katalogdur.

**`detection_types` — çalışma zamanı tür sözlüğü.** Her `(model_id,
class_name)` çifti bir `type_id` alır. Referans katalogdaki 20 tür
`is_catalogued=true`; yeni veya eski veriden gelen bilinmeyen bir sınıf
`is_catalogued=false` olarak otomatik eklenir ve veri kaybolmaz.

**`detected_objects` — tekil tespit fact tablosu.** Olaydaki her nesne ayrı
satırdır: `type_id`, `confidence`, `ts`, `bbox REAL[]` (xyxy piksel) ve
semantic modeller için `area_ratio`. Semantic maskede güven skoru
olmadığından `confidence` NULL'dır. V3 migration güvenli geri dönüş ve audit
için eski `model_id/class_name` kolonlarını da korur; birleşik yabancı anahtar
bu metinlerin `type_id` ile çelişmesine izin vermez. Yeni sorgular sözlük
üzerinden çalışır:

```sql
-- Türe göre son 24 saatin tespitleri ve ortalama doğruluk
SELECT t.model_id, t.class_name, count(*) AS adet,
       round(avg(o.confidence)::numeric, 3) AS ort_dogruluk
FROM detected_objects o
JOIN detection_types t ON t.type_id = o.type_id
WHERE o.ts > now() - interval '24 hours'
GROUP BY t.model_id, t.class_name
ORDER BY adet DESC;

-- Belirli türün zaman içindeki dağılımı
SELECT date_trunc('hour', o.ts) AS saat, count(*)
FROM detected_objects o
JOIN detection_types t ON t.type_id = o.type_id
WHERE t.model_id = 'pothole' AND t.class_name = 'pothole'
GROUP BY 1 ORDER BY 1;
```

Hazır görünümler:

- `vw_detected_objects_flat`: v2'nin dokuz sütunlu okuma sözleşmesi
- `vw_roadvision_model_inventory`: model başına beklenen/gerçek tür sayısı
- `vw_roadvision_detection_type_counts`: toplam, 24 saat ve 7 gün sayımları
- `vw_roadvision_daily_detection_counts`: günlük model/tür özeti
- `vw_roadvision_unknown_detection_types`: katalog bakım kuyruğu
- `vw_roadvision_capture_summary`: capture, model ve JPEG boyut özeti

`(type_id, ts)` indeksi tür/zaman sorgularını, `event_id` indeksi olay
join'lerini destekler. Hacim milyonlarca satıra çıktığında günlük özet için
artımlı rollup ve zaman partitioning ayrı migration olarak değerlendirilebilir.

## Uygulama içindeki Tespit Arşivi

v1.2.2, şema 3'teki tekil tespitleri sağ paneldeki **Tespit Arşivi**
sekmesinde salt-okunur olarak gösterir. DSN tanımlı değilse fetcher kurulmaz;
DB veya şema hatası canlı inference, günlük ve medya yazma hatlarını
etkilemez.

Arşiv sorgusunun ilişki tabanı:

```sql
FROM detected_objects AS o
JOIN detection_events AS e ON e.id = o.event_id
JOIN detection_types AS t ON t.type_id = o.type_id
LEFT JOIN roadvision_model_catalog AS m ON m.model_id = o.model_id
LEFT JOIN media_captures AS mc ON mc.capture_id = e.capture_id
```

“Yalnız görüntüsü olanlar” filtresi özellikle `mc.capture_id IS NOT NULL`
kullanır. `detection_events.capture_id` tek başına yeterli değildir: medya
worker'ı daha sonra yazamamış veya retention capture'ı silmiş olabilir.

Varsayılan sorgu son 24 saat ve yeni→eski zaman sırasıdır. Diğer filtreler
minimum güven, run ve model/tür seçimidir. Sayfalama OFFSET yerine
ASC/DESC ve NULL değerleri açıkça yöneten keyset imleçleri kullanır.
Page ve filtre sayımları tek refresh revision'ında, aynı
`REPEATABLE READ, READ ONLY` transaction'da alınır.

Şema 3 bu özelliğin işlevsel sözleşmesi için yeterlidir. Mevcut indeksler
özellikle zaman/tür yoluna yardımcı olur; confidence, alan oranı veya
Türkçe görünen ad sıralamalarında eşleşen küme ayrıca sıralanabilir.
Milyonlarca satır için bir SLA gerektiğinde temsili veri üzerinde
`EXPLAIN (ANALYZE, BUFFERS)` ölçülmeli ve sonuçlara göre ayrı şema
migration'ında composite indeksler eklenmelidir.

## V2 → V3 güvenli yükseltme

Uygulama ilk v3 bağlantısında migration'ı otomatik yapar. Docker veritabanını
elle yükseltmeden önce çalışan RoadVision süreçlerini kapatın ve yedek alın:

```bash
mkdir -p backups
docker compose exec -T postgres sh -lc \
  'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > backups/roadvision-before-v3.dump

docker compose exec -T postgres sh -lc \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < db/roadvision_schema_v1_2_1.sql
```

Migration tek transaction ve advisory lock altında çalışır. Katalog,
backfill veya FK doğrulaması başarısız olursa `schema_info=3` dahil bütün
değişiklikler geri alınır. V3 eklemeli olduğu için acil uygulama geri dönüşü
veri taşımadan yapılabilir:

```bash
docker compose exec -T postgres sh -lc \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < db/roadvision_schema_v1_2_1_compat_rollback.sql
```

Bu uyumluluk geri dönüşü yeni tablo/kolonları silmez; yalnız v1.2
uygulamasının sürüm kapısını yeniden açar.

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
- Çalışma sonundaki `PostgresSink` checkpoint'i o ana dek kabul edilmiş sıra
  hedefi commit edilene kadar çözülmez. `MediaRecorder` kendi kabul edilmiş
  capture hedefini drain edince engine `archive_ready` üretir; uygulama içi
  arşiv bu onaydan sonra kesin yenileme yapar.
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
