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

`CameraSource`, `ImageSource` ve `VideoSource` bu sözleşmeyi uygular. `SourceFactory` UI'ın somut sınıfları bilmeden doğru kaynağı üretmesini sağlar. Yeni bir RTSP kaynağı eklemek için yalnızca yeni bir `MediaSource` sınıfı ve factory metodu gerekir.

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
- `run_models(frame, model_ids)`
- `set_confidence(value)`
- `set_model_confidence(model_id, value)`
- `set_annotation_enabled(model_id, enabled)`
- `release_models()`

Model örneklerinin tek sahibidir. Lazy loading, cache, model bazlı güven eşikleri, çizim görünürlüğü ve aygıt seçimi burada tutulur. Her model ham kare üzerinde tahmin yapar; görünür çizimler ortak kopyaya sırayla eklenir. Çizimi kapalı bir model yine tahmin ve nesne sayımı yapar. Bu sayede önceki modelin çizdiği kutular sonraki modelin tahmin girdisini kirletmez.

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

Aradaki `Queue(maxsize=1)` back-pressure stratejisidir. Kuyruk doluysa eski bekleyen kare çıkarılır ve yenisi eklenir. Model seçimi kilit altında atomik olarak değiştirilir. Son ham kare saklandığı için statik fotoğraf, model seçimi veya güven eşiği değişince dosyadan tekrar okunmadan yeniden işlenebilir.

Engine; `started`, `frame`, `status`, `source_ended`, `error` ve `stopped` olayları üretir. UI'a dair hiçbir bağımlılığı yoktur.

Engine'e opsiyonel `EventJournal` enjekte edilir. UI günlüğü sağlamadığında `NullJournal` kullanılır; böylece başsız kullanım ve testler disk I/O'suna bağımlı kalmaz. Engine yaşam döngüsü olaylarını `app_event`, her modelin kare özetini `detection`, çalışma kapanışını ise `run_finished` ile bildirir.

## Günlük katmanı

### `LogRecord` ve `LogSink`

`LogRecord`; zaman, seviye, kategori, mesaj, çalışma kimliği, model kimliği ve serbest biçimli payload alanlarından oluşan JSON/veritabanı dostu kayıt modelidir.

`LogSink` hedeflerin `prepare_sink → write_record/flush → release_sink` sözleşmesidir:

- `JsonlFileSink`: satır başına JSON yazar ve boyuta göre dosya döndürür.
- `ConsoleSink`: uyarı ve hataları stderr'e taşır.
- `SessionLogSink`: yazıcı thread'inden gelen kayıtları Tk ana thread'inin tüketebileceği sınırlı kuyruğa aktarır.

### `EventJournal`

`EventJournal` üretici çağrılarını sınırlı kuyruğa `put_nowait` ile bırakır. Tek yazıcı thread tekrar bastırmayı uygular ve kayıtları bütün sink'lere sıralı biçimde iletir. Bir sink'in hatası diğer hedefleri veya uygulamayı düşürmez. Kuyruk taşarsa üretici bekletilmez; düşen kayıt sayısı sonraki yazılabilen kaydın payload alanına eklenir.

`DetectionSuppressor`, `(run_id, model_id)` anahtarında art arda aynı tespit imzasını tek kayda indirger. İmza değişiminde önceki serinin kare ve süre özeti, uzun sabit serilerde heartbeat, çalışma sonunda kapanış özeti üretilir.

Varsayılan günlük kurulumu sırasıyla kullanıcı cache dizini ve geçici dizini dener; dosya hedefi oluşturulamazsa konsol hedefiyle çalışmayı sürdürür. Ayrıntılar [LOGGING.md](LOGGING.md) belgesindedir.

## UI katmanı

### `RoadVisionApp`

Kaynak seçimi, kamera taraması, model seçimleri, güven eşiği, önizleme, durum bilgisi ve oturum günlüğünü yönetir. Sağ içerik alanı **Canlı Önizleme** ve **Oturum Günlüğü** sekmelerine ayrılır. Kamera taraması UI'ı bloklamamak için ayrı kısa ömürlü bir thread'de yapılır. Engine olayları `queue.Queue` ile ana thread'e taşınır; yalnızca Tk ana thread'i widget ve `PhotoImage` nesnelerine dokunur.

`SessionLogSink` journal yazıcı thread'inde Tk nesnelerine dokunmaz. UI'ın mevcut 33 ms polling döngüsü bekleyen `LogRecord` nesnelerini ana thread'de tabloya aktarır. Oturum kuyruğu son 2.000, görünür tablo son 1.000 kaydı tutar; bu sınırlar uzun video/kamera çalışmalarında belleğin sınırsız büyümesini engeller.

## Hata ve kaynak yönetimi

- Kaynak hazırlama ya da inference hatası `error` olayına çevrilir.
- Kamera/video handle'ları hem normal bitişte hem durdurmada serbest bırakılır.
- Uygulama kapanırken worker'lar durdurulur, ardından model cache'i temizlenir.
- `shutdown_complete` olayı işlendiğinde journal kuyruğu boşaltılır, sink'ler flush edilip serbest bırakılır ve ardından Tk penceresi kapatılır.
- Model dosyaları iş başlamadan registry üzerinden doğrulanır.
