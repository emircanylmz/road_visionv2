# RoadVision Tespit Görüntüsü Kaydı — Revize Tasarım ve Uygulama Planı (RVU-0003)

Durum: **uygulandı ve otomatik/gerçek PostgreSQL testleriyle doğrulandı**
Revizyon: 23 Temmuz 2026

## 1. Amaç ve kapsam

Fotoğraf, video ve kamera inference akışında en az bir tespit bulunduğunda:

- modele giren, işaretsiz kareyi;
- tüm seçili modellerin işaretlerini içeren ortak canvas'ı;
- yakalamayı tetikleyen model, imza, kaynak ve kare bilgilerini

PostgreSQL'e kaydetmek. Inference ağ/JPEG I/O'sunda beklememeli, sabit veya
çok benzer sahneler depolamayı şişirmemeli ve veritabanı kesintisi ana
işlemeyi durdurmamalıdır.

“Original” ifadesi kaynak dosyanın birebir baytları anlamına gelmez. Saklanan
işaretsiz görüntü, EXIF yönü düzeltilmiş/modelin işlediği karenin JPEG ve
gerekirse küçültülmüş temsilidir.

## 2. İlk planda bulunan ve bu revizyonda düzeltilen noktalar

1. Sınıf-başına sayım “aynı görüntü” değildir. Aynı iki nesne hareket etse
   eski kapı bunu sonsuza dek aynı sayıyordu. Journal imzası sınıf
   kompozisyonu olarak kaldı; medya imzası normalize/kuantize bbox ve
   semantic footprint ile zenginleştirildi.
2. Minimum aralık sırasında gözlenen yeni imzayı `last_signature` yapmak,
   yeni sahnenin daha sonra da hiç çekilmemesine yol açıyordu. Kapı yalnız
   `last_captured_signature` tutuyor; reddedilen yeni durum aralık dolunca
   tekrar değerlendiriliyor.
3. `capture_id` gate kararından önce journal'a yazılırsa snapshot'ı olmayan
   olaylar oluşuyordu. Artık gate kararı ve recorder kabulünden sonra kare
   başına tek UUID üretiliyor; yalnız tetikleyen modellerin olayına ekleniyor.
4. Model başına iki snapshot satırı fiziksel gerçeği yanlış temsil ediyor ve
   retry'da çoğalma riski taşıyordu. Şema kare başına `media_captures` ve
   model ilişkisi için `media_capture_models` olarak normalize edildi.
5. Snapshot retry'ı idempotent değildi. `capture_id` primary key,
   `(capture_id, model_id)` primary key ve tüm yazımlarda `ON CONFLICT`
   kullanıldı.
6. “Kareyi kopyalamak güvenlidir/gerekmez” varsayımı `MediaSource` soyut
   sözleşmesinde yoktu. Kuyruk kabulünden sonra ham ve işaretli dizilerin
   sahiplik kopyası alınıyor; ayrıca kuyruk hem adet hem tahmini RAM baytıyla
   sınırlandırılıyor.
7. `perf_counter()` veritabanı zamanı değildir. `FramePacket` ayrı monotonic
   zaman ile epoch duvar zamanını taşıyor; yeniden işlemede özgün sequence ve
   zaman korunup `is_reprocess=true` işaretleniyor.
8. Recorder kapanışını hem UI hem engine'in yapması yarış/double-release
   üretiyordu. Tek sahip engine'dir; UI yalnız `shutdown_complete` sonrasında
   journal'ı ve pencereyi kapatır.
9. UI'da annotation kapalıysa eski `result.frame` işaretli değildi. Medya
   etkin olduğunda ModelManager, UI görünürlüğünden bağımsız ortak
   `annotated_frame` üretir.
10. İlk boyut hesabındaki 80 MB/gün ile 500/saat tavanı tutarsızdı.
    400 KB/yakalama varsayımında teorik saatlik tavan yaklaşık 200 MB,
    günlük 4,8 GB'dır. JPEG boyutu içeriğe bağlıdır ve üst sınır değildir.
    Bu nedenle 2 GB toplam blob hedefi manuel değil, her başarılı yazımdan
    sonra otomatik uygulanır.
11. PostgreSQL TOAST JPEG'i anlamlı ölçüde tekrar sıkıştırmak zorunda
    değildir; asıl faydası büyük BYTEA değerini satır dışı yönetmesidir.
12. pgAdmin BYTEA'yı görsel önizleme olarak açmaz. Sorgu yanında
    `scripts/export_media.py` eklendi.
13. Sınırlı journal/DB/media kuyrukları varken “olay kaydı asla düşmez”
    garantisi doğru değildir. Günlük ve görüntü hatları açıkça best-effort
    olarak belgelenmiştir.

## 3. Mimari

### 3.1 Engine akışı

Her inference sonucu için sıra şöyledir:

1. Her model için journal imzası ve daha zengin medya imzası hesaplanır.
2. `SnapshotGate.evaluate(...)`, bu karede görüntü tetikleyen modelleri
   seçer. Kota fiziksel kare başına sayılır; model sayısıyla çarpılmaz.
3. Seçim boş değilse kare başına tek UUID oluşturulur.
4. `Snapshot` (capture metadata + model ilişkileri) kurulur.
5. Recorder kuyruk/bellek bütçesi işi kabul ederse karelerin sahiplik
   kopyasını alır ve `True` döner.
6. Yalnız kabulden sonra gate state'i commit edilir ve aynı `capture_id`
   tetikleyen modellerin `journal.detection` payload'ına verilir.
7. Kuyruk doluysa gate commit edilmez; aynı yeni sahne sonraki karede tekrar
   denenebilir. Capture kimliği olay kaydına eklenmez.

Journal sınıf kompozisyonu aynı olsa bile `capture_id` taşıyan gözlemi
`dedup=capture` olarak geçirir. Böylece bbox/semantic hareketi nedeniyle
alınan her görüntünün `detection_events` korelasyon satırı bulunur.

Medya yolundaki tüm hatalar ayrı `try/except` sınırındadır ve warning'e
dönüşür; inference run'ı `ERROR` durumuna geçirilmez.

### 3.2 `roadvision/media.py`

- `FrameEncoder`: BGR/gri numpy kareyi uzun kenar sınırı ve JPEG kalitesiyle
  kodlar; SHA-256, boyut ve MIME üretir.
- `snapshot_signature`: sınıf + 5% konum/boyut kovaları + semantic alan
  footprint'i. Küçük koordinat titreşimini bastırır, belirgin hareketi ayırır.
- `SnapshotGate`: boş kare, aynı yakalanmış imza, minimum aralık, run ve
  kayan 60 dakikalık global tavan kuralları.
- `MediaRecorder`: tek worker, adet+RAM sınırlı kuyruk, async JPEG/store,
  drop/error uyarıları ve drain/release yaşam döngüsü.
- `MediaSink`: depolama soyutlaması.
- `DbMediaSink`: tembel ve süre-sınırlı bağlantı, idempotent transaction,
  sınırlı retry, otomatik retention/quota.
- `NullRecorder`: medya kapalıyken etkisiz uygulama.

### 3.3 Gate kuralları

Sıra:

1. `object_count <= 0`: reddet.
2. İmza `last_captured_signature` ile aynı: reddet.
3. Dinamik kaynakta son başarılı yakalamadan minimum süre geçmediyse reddet;
   yeni imzayı state'e yazma.
4. Run veya kayan saat kotası doluysa reddet ve sınır başına tek uyarı üret.
5. Kalan tetikleyici modelleri tek fiziksel capture grubu olarak kabul et.

Fotoğrafta minimum aralık uygulanmaz. Yeniden işlem aynı imzadaysa reddedilir;
eşik/model değişimi tespit geometrisini değiştirirse yeni capture alınabilir.

## 4. PostgreSQL şema v2

Migration'lar `schema_info` üzerinden sırayla, tek transaction ve PostgreSQL
advisory lock altında uygulanır. Uygulamanın bildiğinden daha yeni bir şema
görülürse sessizce devam edilmez.

```sql
media_blobs(
  id PK, sha256 UNIQUE, mime, width, height, byte_size, data BYTEA, created_at
)

media_captures(
  capture_id UUID PK, ts, run_id, source_name, source_kind,
  frame_sequence, is_reprocess,
  original_media_id FK media_blobs RESTRICT,
  annotated_media_id FK media_blobs RESTRICT
)

media_capture_models(
  capture_id FK media_captures CASCADE,
  model_id, signature JSONB, object_count,
  PK(capture_id, model_id)
)

detection_events.capture_id UUID NULL
```

Blob upsert'i `sha256` üzerinden `RETURNING id` kullanır. Aynı capture'ın
belirsiz commit sonrası retry'ı capture/model primary key'leri sayesinde
çoğalma üretmez. Journal ve medya farklı asenkron hatlarda olduğundan
`detection_events.capture_id` üzerinde FK yoktur; sorgular `LEFT JOIN`
kullanır ve iki hattan birinin düşmesini toleranslı karşılar.

## 5. Depolama ve bellek sınırları

- Uzun kenar: varsayılan 1280 px.
- JPEG kalite: varsayılan 80.
- İçerik-adresli blob tekilleştirme.
- Minimum dinamik yakalama aralığı: 2 saniye/model.
- Run kotası: 200 fiziksel kare.
- Kayan saat kotası: 500 fiziksel kare.
- Recorder kuyruğu: 8 iş ve ayrıca 256 MB tahmini ndarray bütçesi.
- Retention: 30 gün.
- Blob hedefi: `SUM(byte_size) <= 2048 MB`.

Her başarılı DB yazımından sonra:

1. retention süresinden eski capture grupları silinir;
2. iki FK'den de referansı kalmayan blob'lar silinir;
3. blob toplamı hedefi aşıyorsa en eski capture grupları bütün olarak silinir;
4. shared blob yalnız son referansı kalkınca silinir.

Bu kota JPEG payload toplamıdır; tablo/TOAST/indeks/WAL ve yedek overhead'i
dahil değildir. `DELETE` fiziksel dosyayı anında küçültmez, alanı PostgreSQL
tarafından yeniden kullanılabilir hale getirir.

## 6. Yapılandırma

Tamamı ortam değişkenidir ve aralık doğrulaması vardır:

| Değişken | Varsayılan |
|---|---:|
| `ROADVISION_MEDIA` | `db` |
| `ROADVISION_MEDIA_JPEG_QUALITY` | `80` |
| `ROADVISION_MEDIA_MAX_EDGE` | `1280` |
| `ROADVISION_MEDIA_MIN_INTERVAL_S` | `2.0` |
| `ROADVISION_MEDIA_MAX_PER_RUN` | `200` |
| `ROADVISION_MEDIA_MAX_PER_HOUR` | `500` |
| `ROADVISION_MEDIA_QUEUE_SIZE` | `8` |
| `ROADVISION_MEDIA_QUEUE_MAX_MB` | `256` |
| `ROADVISION_MEDIA_RETENTION_DAYS` | `30` |
| `ROADVISION_MEDIA_MAX_TOTAL_MB` | `2048` |
| `ROADVISION_MEDIA_SHUTDOWN_TIMEOUT_S` | `10` |

`ROADVISION_MEDIA=off` veya boş `ROADVISION_DB_DSN`, `NullRecorder` seçer.
Geçersiz medya ayarı warning üretir ve görüntü kaydını güvenli biçimde
kapatır.

## 7. Yaşam döngüsü

1. UI journal'ı ve recorder nesnesini kurar.
2. Engine recorder'ı prepare eder ve tek yaşam döngüsü sahibi olur.
3. Run kapanırken capture/inference worker'ları join edilir, frame kuyruğu
   boşaltılır, gate run state'i temizlenir.
4. Uygulama shutdown'ında recorder kuyruğu drain edilir ve sink kapatılır.
5. Model kaynakları bırakılır.
6. Engine `shutdown_complete` yayınlar.
7. UI journal'ı kapatır ve pencereyi yok eder.

## 8. Operasyon

- Güncel şema: `db/schema.sql`
- Ayrıntılı sorgular: `DATABASE.md`
- Dry-run temizlik: `python3 scripts/prune_media.py`
- Gerçek temizlik: `python3 scripts/prune_media.py --apply`
- JPEG çıkarma:
  `python3 scripts/export_media.py CAPTURE_UUID --output-dir exports`

pgAdmin'de tablolar ve metadata doğrudan incelenir; JPEG önizlemesi export
scripti veya ileride eklenecek UI/API üzerinden yapılır.

## 9. Test kabul ölçütleri

- Encoder boyut, kalite, deterministik hash ve gri/renk kareleri işler.
- Gate aynı imzayı reddeder; interval içinde reddedilen stabil yeni imzayı
  süre dolunca kabul eder; çoklu modeli tek fiziksel kota sayar.
- Aynı count/farklı bbox ve semantic footprint değişimi yakalanır.
- Recorder kuyruk sınırı, ndarray sahiplik kopyası, drain/release ve hata
  izolasyonu doğrulanır.
- İki model/tek kare tek UUID ve tek submit üretir.
- Capture reddinde journal payload'ında `capture_id` bulunmaz.
- Annotation görünmezken UI karesi temiz, medya canvas'ı işaretlidir.
- v1→v2 migration, tekrar çağrı ve future-version guard doğrulanır.
- Gerçek Docker PostgreSQL'de migration, idempotent retry, blob dedupe,
  event/capture JOIN'i ve prune çalışır.

## 10. Bilinçli sınırlar

- JPEG ve günlük yazımı best-effort'tur; tam teslim garantisi için ileride
  kalıcı local spool/transactional outbox gerekir.
- İşaretli görüntü model başına ayrı değil, seçili modellerin ortak canvas'ıdır.
- Capture'daki `run_id` süreç yereldir; küresel korelasyonun asıl anahtarı
  UUID `capture_id`'dir. Kalıcı session/run UUID gelecekte ayrıca eklenebilir.
- Hacim veya yedek süresi büyürse `MediaSink` arkasında File/S3/MinIO
  uygulamasına geçilebilir; engine/gate sözleşmesi değişmez.
