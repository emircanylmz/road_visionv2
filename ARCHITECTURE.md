# RoadVision sınıf tasarımı

Bu tasarımda her bileşen `prepare → use/stream → release` yaşam döngüsünü izler. Böylece kamera örneğindeki modüler yaklaşım kaynaklara ve modellere de aynı biçimde uygulanır.

## Kaynak katmanı

### `Camera`

- `get_camera_indexes(max_index)`: okunabilir tüm kamera indekslerini ve çözünürlüklerini döndürür.
- `get_camera_index(max_index)`: istenen API adıyla aynı listeyi döndüren uyumluluk alias'ıdır.
- `prepare_camera(index, width, height, fps)`: kamerayı açar ve capture ayarlarını yapar.
- `read_frame()`: tek kare okur.
- `get_stream(stop_event)`: durdurma sinyaline kadar kare üretir.
- `release_camera()`: aygıtı deterministik olarak serbest bırakır.

### `MediaSource`

Bütün medya tiplerinin soyut sözleşmesidir:

- `prepare_source()`
- `get_stream(stop_event)`
- `release_source()`
- `is_static`

`CameraSource`, `GStreamerCameraSource`, `ImageSource` ve `VideoSource` bu
sözleşmeyi uygular. `GStreamerCameraSource`, Jetson CSI için
`nvarguscamerasrc` hattını veya dışarıdan verilen özel bir pipeline'ı
`cv2.CAP_GSTREAMER` ile açar. `ROADVISION_CSI_SENSORS` ile tanımlanan
sensörler PyQt ve Tk kamera listesine eklenir. `SourceFactory` UI'ın somut
sınıfları bilmeden doğru kaynağı üretmesini sağlar.

## Model katmanı

### `ModelConfigLoader`, `ModelSpec` ve `ModelRegistry`

`ModelConfigLoader`, `models.json` kataloğunu UTF-8 olarak okur, şema ve alan doğrulamasını yapar, relatif ağırlık yollarını JSON klasörüne göre çözümler ve etkin girdileri immutable `ModelSpec` nesnelerine dönüştürür. `ROADVISION_MODEL_CONFIG` ortam değişkeniyle alternatif katalog seçilebilir.

`ModelSpec`, ağırlık yolu, task, giriş boyutu, görünen ad ve overlay rengini taşır. `ModelRegistry`:

- `get_available_models()`
- `get_model(model_id)`
- `validate_models(model_ids)`

metotlarıyla model keşfini ve doğrulamayı UI'dan ayırır.

### `ModelAdapter`

Yeni bir inference backend'inin uygulaması gereken soyut sözleşmedir:

- `prepare_model()`
- `predict(frame)`
- `annotate(frame, result)`
- `release_model()`

`YoloModelAdapter` bu sözleşmenin Ultralytics uygulamasıdır. Detection kutularını ve semantic maskeleri ortak canvas üzerine model rengiyle çizer. ONNX veya TensorRT desteği gerektiğinde aynı sözleşmeyi uygulayan yeni adaptör eklenebilir.

### `ModelManager`

- `get_available_models()`
- `prepare_model(model_id)` / `prepare_models(model_ids)`
- `run_models(frame, model_ids, capture_annotations=False)`
- `set_confidence(value)`
- `set_model_confidence(model_id, value)`
- `set_annotation_enabled(model_id, enabled)`
- `release_models()`

Model örneklerinin tek sahibidir. Başlangıç model kümesi ilk gerçek kareden
önce hazırlanır; CUDA adaptörleri kernel/cuDNN ilk çağrı maliyetini önden
ödemek için sentetik kareyle bir kez ısındırılır. Lazy loading, cache, model
bazlı güven eşikleri, çizim görünürlüğü ve aygıt seçimi burada tutulur. Her
model ham kare üzerinde tahmin yapar; görünür çizimler ortak kopyaya sırayla
eklenir. Çizimi kapalı bir model yine tahmin ve nesne sayımı yapar. Medya kaydı
etkinse ayrıca UI görünürlüğünden bağımsız, tüm model işaretlerini taşıyan
`annotated_frame` üretilir.

## İşleme katmanı

### `ProcessingEngine`

- `start(source, model_ids)`
- `update_models(model_ids)`
- `set_confidence(value)`
- `set_model_confidence(model_id, value)`
- `set_annotation_enabled(model_id, enabled)`
- `stop()`
- `shutdown()`

İki worker yönetir:

1. **Capture worker:** kaynaktan kareleri okur.
2. **Inference worker:** en güncel kareyi seçili modellerle işler.

Aradaki `Queue(maxsize=1)` back-pressure stratejisidir. Kuyruk doluysa eski bekleyen kare çıkarılır ve yenisi eklenir. Model seçimi kilit altında atomik olarak değiştirilir. Son `FramePacket` (kare, kaynak sequence'i, monotonic ve duvar zamanı) saklandığı için statik fotoğraf, model seçimi veya güven eşiği değişince dosyadan tekrar okunmadan ve özgün korelasyon bilgisi kaybolmadan yeniden işlenebilir.

Engine; `started`, `frame`, `status`, `source_ended`, `error` ve `stopped` olayları üretir. UI'a dair hiçbir bağımlılığı yoktur.

Engine'e opsiyonel `EventJournal`, `MediaRecorder` ve `SnapshotGate` enjekte edilir. UI bunları sağlamadığında no-op karşılıklar kullanılır; böylece başsız kullanım ve testler I/O'ya bağımlı kalmaz. Engine yaşam döngüsü olaylarını `app_event`, her modelin kare özetini `detection`, çalışma kapanışını ise `run_finished` ile bildirir. Recorder'ın tek yaşam döngüsü sahibi engine'dir.

## Günlük katmanı

### `LogRecord` ve `LogSink`

`LogRecord`; zaman, seviye, kategori, mesaj, çalışma kimliği, model kimliği ve serbest biçimli payload alanlarından oluşan JSON/veritabanı dostu kayıt modelidir.

`LogSink` hedeflerin `prepare_sink → write_record/flush → release_sink` sözleşmesidir:

- `JsonlFileSink`: satır başına JSON yazar ve boyuta göre dosya döndürür.
- `ConsoleSink`: uyarı ve hataları stderr'e taşır.
- `SessionLogSink`: yazıcı thread'inden gelen kayıtları Tk ana thread'inin tüketebileceği sınırlı kuyruğa aktarır.
- `PostgresSink`: günlükleri ayrı sınırlı kuyruk ve flusher thread'iyle
  `log_records`, `detection_events` ve `detected_objects` tablolarına açar.
  Şema v3'te tekil tespitler ortak fact tablosunda kalır; model/sınıf kimliği
  `detection_types.type_id` sözlüğüne bağlanır. Eski metin kolonları güvenli
  rollback için birleşik FK korumasıyla geçici olarak tutulur.

### `EventJournal`

`EventJournal` üretici çağrılarını sınırlı kuyruğa `put_nowait` ile bırakır. Tek yazıcı thread tekrar bastırmayı uygular ve kayıtları bütün sink'lere sıralı biçimde iletir. Bir sink'in hatası diğer hedefleri veya uygulamayı düşürmez. Kuyruk taşarsa üretici bekletilmez; düşen kayıt sayısı sonraki yazılabilen kaydın payload alanına eklenir.

`PersistenceCheckpoint`, bir çalışma sonundaki journal sınırını asenkron
olarak taşır. Journal marker'ı önceki kayıtları sink'lere teslim ettikten
sonra `PostgresSink` kendi sıralı kayıt hedefinin commit edilmesini bekler;
retry/backoff UI'a veya inference'a taşınmaz.

`DetectionSuppressor`, `(run_id, model_id)` anahtarında art arda aynı tespit imzasını tek kayda indirger. İmza değişiminde önceki serinin kare ve süre özeti, uzun sabit serilerde heartbeat, çalışma sonunda kapanış özeti üretilir.

Varsayılan günlük kurulumu sırasıyla kullanıcı cache dizini ve geçici dizini dener; dosya hedefi oluşturulamazsa konsol hedefiyle çalışmayı sürdürür. Ayrıntılar [LOGGING.md](LOGGING.md) belgesindedir.

## Medya kayıt katmanı

### `SnapshotGate`, `MediaRecorder` ve `MediaSink`

`SnapshotGate`, journal tekrar imzasından ayrı olarak sınıf + kuantize bbox/semantic footprint imzası kullanır. Boş/aynı sahneyi, minimum aralığı ve fiziksel run/saat kotalarını inference thread'inde I/O yapmadan değerlendirir. State yalnız recorder işi kabul ettiğinde commit edilir.

`MediaRecorder`, kabul edilen ham ve ortak işaretli karelerin sahiplik kopyasını adet ve RAM-baytı sınırlı kuyruğa alır. Tek worker JPEG kodlar ve `MediaSink`'e yazar. `DbMediaSink`, SHA-256 blob tekilleştirmesi, capture/model idempotency ve otomatik süre/boyut kotasını uygular. `NullRecorder` medya kapalıyken no-op'tur. Ayrıntılar [MEDYA_TASARIM_PLANI.md](MEDYA_TASARIM_PLANI.md) ve [DATABASE.md](DATABASE.md) belgelerindedir.

Recorder checkpoint'i çağrı anına kadar kabul edilmiş sequence hedefini
yakalar ve tek worker bu hedefe kadar bütün store denemelerini bitirdiğinde
çözülür. Engine journal ve medya checkpoint'leri birlikte başarılı olunca
`archive_ready` üretir; arşiv görünümü kalıcı yazıların önüne geçmez.

## UI katmanı

### `ArchiveQuery`, `ArchiveFetcher` ve saf arşiv durumu

`roadvision.archive` arşiv filtrelerini, allowlist'li sort ifadelerini,
ASC/DESC ve NULL-aware keyset imleçlerini Tk'den bağımsız tutar. Liste ve
tür-facet sayımları aynı FROM/WHERE sözleşmesini paylaşır; görüntü filtresi
yalnız `detection_events.capture_id` değerine değil, yaşayan
`media_captures` satırına bakar. DB fonksiyonları açık bağlantı alır ve
transaction yönetmez.

`ArchiveFetcher` bağlantının tek sahibidir. Tür ağacı ve sonuç yenilemesi
ayrı nesiller taşır; page + isteğe bağlı counts tek refresh revision'ında,
aynı `REPEATABLE READ, READ ONLY` transaction'da çalışır. Bekleyen işler
sınırsız queue yerine tür başına latest slotta birleştirilir. Worker yalnız
sınırlı sonuç kanalına yazar; Tk veya journal callback'i çağırmaz.

`TypeSelectionModel`, `PaginationState` ve `ArchiveState` üç durumlu seçim,
request-cursor geçmişi ve draft/applied filtre durumunu widget'lardan ayırır.
Bu sayede arşivin yarış ve gezinme kuralları grafik ortam olmadan test
edilebilir.

### `ArchivePage`

Sağ Notebook'taki üçüncü sekmedir. Filtre ağacı ve sonuç tablosu bir
`Panedwindow` içinde yer alır; sonuçta yatay/dikey scrollbar bulunur.
Değişiklikler iptal edilebilir Tk debounce ile tek refresh'e indirgenir.
Sekme ilk kez görünür olduğunda önce tür ağacı, sonra varsayılan son-24-saat
sorgusu istenir. Worker sonuçları mevcut 33 ms UI polling turunda ve yalnız
Tk ana thread'inde uygulanır.

### `RoadVisionApp`

Kaynak seçimi, kamera taraması, model seçimleri, güven eşiği, önizleme,
durum bilgisi, oturum günlüğü ve arşiv entegrasyonunu yönetir. Sağ içerik
alanı **Canlı Önizleme**, **Oturum Günlüğü** ve **Tespit Arşivi** sekmelerine
ayrılır. Kamera taraması UI'ı bloklamamak için ayrı kısa ömürlü bir thread'de
yapılır. Engine olayları `queue.Queue` ile ana thread'e taşınır; yalnızca Tk
ana thread'i widget ve `PhotoImage` nesnelerine dokunur.

`RoadVisionApp` hem oturum satırlarının hem arşiv satırlarının kullandığı
ortak snapshot controller'ının sahibidir; alt sayfalar
`SnapshotViewerWindow` sınıfını import etmez.

`SessionLogSink` journal yazıcı thread'inde Tk nesnelerine dokunmaz. UI'ın mevcut 33 ms polling döngüsü bekleyen `LogRecord` nesnelerini ana thread'de tabloya aktarır. Oturum kuyruğu son 2.000, görünür tablo son 1.000 kaydı tutar; bu sınırlar uzun video/kamera çalışmalarında belleğin sınırsız büyümesini engeller.

## Hata ve kaynak yönetimi

- Kaynak hazırlama ya da inference hatası `error` olayına çevrilir.
- Kamera/video handle'ları hem normal bitişte hem durdurmada serbest bırakılır.
- Uygulama kapanırken run worker'ları durdurulur, medya kuyruğu drain/release edilir, ardından model cache'i temizlenir.
- Kapanış başlarken snapshot/arşiv fetcher'ları yeni istek kabulünü ve
  bekleyen işleri anında keser; Tk thread'i çalışan DB sorgusunu beklemez.
- `shutdown_complete` olayı işlendiğinde fetcher'lara kısa bir bounded join
  verilir; ardından journal kuyruğu boşaltılır, sink'ler flush edilip serbest
  bırakılır ve Tk penceresi kapatılır.
- Model dosyaları iş başlamadan registry üzerinden doğrulanır.
