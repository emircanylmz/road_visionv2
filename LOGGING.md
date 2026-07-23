# RoadVision günlük ve oturum ekranı

RoadVision; uygulama yaşam döngüsünü, durum ve hata olaylarını ve model tespit özetlerini hem kalıcı JSONL dosyasına hem de UI'daki canlı oturum ekranına yazar. Günlük hattı inference, capture ve Tk ana thread'lerini disk I/O'suyla bekletmeyecek şekilde tasarlanmıştır.

## Kayıt akışı

```text
ProcessingEngine / RoadVisionApp
             │
             ▼  put_nowait
       EventJournal kuyruğu
             │
             ▼  tek yazıcı thread
     DetectionSuppressor
       ┌─────┼──────────┬──────────────┐
       ▼     ▼          ▼              ▼
   JSONL   stderr  PostgresSink  SessionLogSink
                                        │
                                        ▼  33 ms UI polling
                                Oturum Günlüğü tablosu
```

`app_event`, `detection` ve `run_finished` çağrıları yalnız sınırlı journal kuyruğuna kayıt bırakır. JSON serileştirme, tekrar bastırma ve sink yazımları tek journal worker'ında gerçekleşir. Bu nedenle model çıkarımı günlük dosyasının yazılmasını beklemez.

## Kayıt şeması

Her satır bir `LogRecord` nesnesinin JSON karşılığıdır:

| Alan | Açıklama |
| --- | --- |
| `time` | UTC ISO-8601 kayıt zamanı |
| `level` | `debug`, `info`, `warning` veya `error` |
| `category` | `app` veya `detection` |
| `message` | İnsan tarafından okunabilir olay özeti |
| `run_id` | İlgili çalışma kimliği; uygulama-geneli kayıtlarda `null` |
| `model_id` | İlgili model kimliği; uygulama olaylarında `null` |
| `payload` | Olay türüne özel yapılandırılmış ayrıntılar |
| `ingest_key` | JSONL/PostgreSQL retry ve backfill işlemlerinde kullanılan tekil kayıt kimliği |

Örnek tespit kaydı:

```json
{"time":"2026-07-22T12:11:55.011571+00:00","level":"info","category":"detection","message":"Tabela ve Trafik Işığı: 1 tespit","run_id":1,"model_id":"traffic_sign","payload":{"object_count":1,"elapsed_ms":690.0,"signature":1,"dedup":"changed","repeated_frames":1},"ingest_key":"live:93f91b6c30e24fc1a85550c0df18b728"}
```

## Kalıcı dosyalar

Varsayılan dosya:

```text
~/.cache/roadvision/logs/roadvision.jsonl
```

Dosya 5 MB'a ulaştığında mevcut içerik `roadvision.jsonl.1` adına taşınır ve yeni `roadvision.jsonl` açılır. Varsayılan cache dizini kullanılamazsa sistem geçici dizini denenir; dosya hedefi hazırlanamazsa uygulama yalnız konsol sink'iyle çalışmaya devam eder.

JSONL satırları `jq`, Python, SQLite içe aktarma araçları veya başka veri işleme sistemleriyle doğrudan tüketilebilir.

## UI oturum günlüğü

Sağ panelde iki sekme bulunur:

- **Canlı Önizleme:** işlenen görüntü ve performans bilgisi.
- **Oturum Günlüğü:** mevcut uygulama oturumunda oluşan kayıtların canlı tablosu.

Tablo saat, seviye, kategori, run, model, mesaj ve payload ayrıntılarını gösterir. Debug, info, warning ve error kayıtları farklı renklerle işaretlenir. Yeni kayıt geldiğinde görünüm otomatik olarak en alta kayar.

**Ekranı Temizle** yalnız mevcut tablonun satırlarını temizler. JSONL dosyasını, rotasyon yedeğini veya journal'ın diğer sink'lerini silmez.

`SessionLogSink` son 2.000 bekleyen kaydı tutar. UI tablosu son 1.000 satırla sınırlıdır. Kuyruk dolarsa en eski UI kaydı atılarak en yeni olayların görünür kalması sağlanır.

### Tespit görüntüsünü açma

Payload'ında `capture_id` bulunan tespit satırları dar **📷** sütununda
işaretlenir. Satıra çift tıklamak veya satırı seçip **Görüntüyü Aç**
düğmesine basmak PostgreSQL'deki işaretli ve orijinal JPEG çiftini tek bir
görüntüleyici penceresinde açar. Penceredeki **Yenile** bağlantıyı tekrar
dener; **Diske Kaydet** yalnız o anda seçili olan görünümü
`CAPTURE_UUID-annotated.jpg` veya `CAPTURE_UUID-original.jpg` düzeniyle
kaydeder.

Okuma ayrı bir worker thread'de yapılır; Tk ana thread'i PostgreSQL'i
beklemez. Hızlı ardışık seçimlerde yalnız son isteğin sonucu uygulanır ve son
16 capture bellek içi LRU önbellekte tutulur. Recorder henüz yazmayı
bitirmediyse son 15 saniyelik bir satır 1,5 saniye sonra bir kez otomatik
yeniden denenir.

`ROADVISION_DB_DSN` tanımlı değilse görüntüleyici kurulmaz. 📷 sütunu boş
kalır ve açma girişimi yalnız durum çubuğunda bilgi verir; JSONL günlük ve
uygulamanın diğer işlevleri etkilenmez.

## Tespit tekrarlarının bastırılması

Her model ve çalışma için varsayılan imza, tekil nesneler varsa sınıf-başına
sayım demeti; nesne çıkarımı yoksa `object_count` değeridir. Art arda aynı
imza geldiğinde her kare için yeni satır yazılmaz:

- İlk imza veya imza değişimi `dedup=changed` kaydı üretir.
- Sınıf imzası aynı olsa da medya kapısı yeni bir mekânsal kare yakalarsa,
  olay↔görüntü ilişkisini korumak için `dedup=capture` kaydı üretilir.
- Sabit durum 30 saniyeyi aşarsa `dedup=heartbeat` kaydı üretilebilir.
- İmza değiştiğinde önceki serinin kare sayısı ve süresi `previous` alanına eklenir.
- Çalışma bittiğinde açık seri `closed_by=run_finished` özetiyle kapatılır.

Bu yaklaşım uzun kamera/video çalışmalarında aynı tespitin binlerce kez yazılmasını engellerken anlamlı durum değişimlerini korur.

## Hata ve taşma davranışı

- Bir sink hata verirse diğer sink'lere yazım devam eder.
- Journal worker'ındaki bir hata uygulamayı düşürmez.
- Journal kuyruğu dolarsa üretici thread bekletilmez.
- Düşen kayıt sayısı, sonraki yazılabilen kaydın payload alanına `dropped_before_this` olarak eklenir.
- Uygulama kapanırken journal kuyruğu kapatma işaretine kadar tüketilir; sink'ler flush edilip serbest bırakılır.

## Yeni hedef ekleme

Veritabanı, HTTP veya MQTT hedefi eklemek için `LogSink` sözleşmesi uygulanır:

```python
class DatabaseSink(LogSink):
    def prepare_sink(self) -> None: ...
    def write_record(self, record: LogRecord) -> None: ...
    def flush(self) -> None: ...
    def release_sink(self) -> None: ...
```

Ardından hedef `EventJournal.add_sink()` ile kaydedilir. Engine ve UI'daki günlük üreten kodun değiştirilmesi gerekmez.

## Doğrulama

```bash
python3 -m unittest discover -s tests -v
```

Testler JSONL yazımı ve rotasyonu, seviye filtreleme, sink hata izolasyonu, journal kuyruk taşması, tekrar bastırma, çalışma sonu özeti, oturum kuyruğu sınırı ve kayıtların UI tablosuna canlı aktarımını kapsar.
