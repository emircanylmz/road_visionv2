# Değişiklik günlüğü

## [Yayımlanmadı]

## [2.0.1] - 2026-07-29

### Eklendi

- Başlangıçta seçili modeller `models_ready_event` açılmadan önce hazırlanır;
  CUDA modelleri sentetik kareyle ısındırılarak kernel/cuDNN ilk çağrı
  maliyeti gerçek akıştan önce ödenir. Hazırlık hataları hiçbir gerçek kare
  işlenmeden açık bir motor hatasına dönüştürülür.
- Jetson CSI kameraları için `GStreamerCameraSource`, hazır
  `nvarguscamerasrc` pipeline kurucusu ve PyQt/Tk kamera listesine
  `ROADVISION_CSI_SENSORS` üzerinden sensör ekleme desteği eklendi.
  `appsink drop=1 max-buffers=1`, motorun latest-frame ilkesini GStreamer
  tarafında da korur.
- PyQt6 tabanlı yeni arayüz (`roadvision/qt/`, "RoadVision Arayüz v2"
  tasarımı): ikon raylı 4 sayfa (Canlı Önizleme, Çalışma Özeti, Oturum
  Günlüğü, Tespit Arşivi), QGraphicsView üzerinde zoom/pan destekli
  önizleme (BGR kare renk dönüşümü ve PIL olmadan `Format_BGR888` ile
  çizilir), sparkline'lı telemetri kartları, model kartlarında model
  rengiyle güven kaydırağı, QTableView tabanlı günlük ve ayrıntı
  çekmecesi, arşiv sayfasının aynı `ArchiveState`/keyset akışıyla Qt
  portu, İşaretli/Orijinal/Yan yana görünümlü tespit görüntüsü diyaloğu
  ve CSV/JSONL çalışma özeti dışa aktarımı. Motor sözleşmeleri değişmedi:
  aynı olay kuyruğu, 33 ms tek kalp atışı, run-id filtresi ve iki fazlı
  kapanış. `ROADVISION_UI=tk` eski Tk arayüzünü seçer; PyQt6 kurulu
  değilse giriş noktası kendiliğinden Tk'ye döner.

- `models.json` kataloğuna isteğe bağlı `sha256` alanı: ağırlık dosyası
  YOLO'ya (dolayısıyla pickle'a) verilmeden önce doğrulanır; `git lfs pull`
  çalıştırılmamış klonlardaki LFS işaretçileri kafa karıştırıcı torch hatası
  yerine net bir yönlendirmeyle yakalanır. Depodaki dört model için özetler
  eklendi.
- `ROADVISION_MEDIA_PRUNE_INTERVAL_S` (varsayılan 60): medya kota/retention
  temizliği her yazımdan sonra değil, en fazla bu aralıkta bir çalışır. 0
  değeri eski davranışı korur; yeni bağlantının ilk yazımı kotayı hemen
  uygular.

### Değiştirildi

- CUDA üzerinde `torchvision::nms` bulunamadığında çalışma CPU'ya sessizce
  düşmek yerine Jetson/NVIDIA Torch-Torchvision kurulum yönlendirmesiyle
  durur; MPS fallback davranışı değişmedi.
- Linux/V4L2 kameralarında çözünürlükten önce varsayılan MJPG formatı istenir.
  `ROADVISION_CAMERA_FOURCC` başka bir dört karakterlik formatı zorlayabilir
  veya boş değerle isteği kapatabilir; macOS ve Windows etkilenmez.
- `write_batch`, psycopg pipeline destekleyen bağlantılarda statement'ları
  toplu gönderir; 500 kayıtlık JSONL backfill grupları kayıt başına bir
  gidiş-dönüş yerine birkaç senkronizasyonla yazılır. Idempotency ve satır
  içerikleri sıralı yolla birebir aynıdır; test fake'leri gibi pipeline
  sunmayan bağlantılar eski sıralı yolda kalır.
- Canlı önizleme karesi tam çözünürlükte LANCZOS küçültme yerine önce
  `cv2.resize` (INTER_AREA) ile önizleme boyutuna indirilir; renk dönüşümü
  ve PIL kopyası küçük karede yapıldığından Tk ana thread'inin kare başına
  maliyeti düşer.
- torchvision >= 0.20 kurulumlarında detect görevleri Apple MPS üzerinde
  bırakılır; "mps + cpu(det)" zorlaması yalnız yerel MPS NMS çekirdeği
  taşımayan eski sürümlerde uygulanır. `predict` içindeki çalışma zamanı
  CPU fallback'i güvenlik ağı olarak korunur.
- `scripts/*.py --dsn` yardım metinleri, parolalı DSN'in komut satırında
  süreç listesi ve kabuk geçmişinde görünür olduğunu belirtir ve
  `ROADVISION_DB_DSN` ortam değişkenini önerir.
- `scripts/run_with_db.sh`, proje içindeki `.venv/bin/python` bulunduğunda
  PostgreSQL sürücüsünün ve uygulama bağımlılıklarının aynı sanal ortamdan
  yüklenmesini garanti eder; `.venv` yoksa `python3` geri dönüşü korunur.

### Düzeltildi

- Tk arşivinde minimum güven sürgüsü yalnız filtre etkin olduğunda
  kullanılabilir; Run filtresi her iki UI'da "Çalışma no" olarak açıklanır.
- `test_multiple_cpu_models_use_distinct_pool_workers`, 6'dan az mantıksal
  çekirdekli makinelerde (küçük CI runner'ları) model havuzu tek worker'a
  düştüğü için Barrier zaman aşımıyla başarısız oluyordu; çekirdek sayısı
  testte sabitlendi.
- `ingest_key_for` içindeki SHA-1 `usedforsecurity=False` ile işaretlendi
  (idempotency anahtarıdır; FIPS ortamları ve güvenlik tarayıcıları için).
  Kullanılmayan iki import temizlendi; `run_models` içindeki `zip` çağrısına
  `strict=True` eklendi.

### Doğrulama

- 255 otomatik test PostgreSQL sürücüsü kurulu ortamda atlama olmadan geçti.
- PyQt6 arayüzü ekran göstermeden başlatılıp güvenli kapatıldı; arşiv,
  snapshot ve medya bileşenlerinin PostgreSQL ile etkinleştiği doğrulandı.
- Dört gerçek model CPU üzerinde sentetik kareyle yüklenip çıkarım yaptı.
- PostgreSQL 17.10, şema sürümü 3 ve mevcut `model_v2` verileriyle canlı
  bağlantı doğrulandı; yeni veritabanı migration'ı gerekmedi.

## [1.2.2] - 2026-07-27

### Eklendi

- PostgreSQL'e yazılmış tekil tespitleri uygulama içinden açan üçüncü
  **Tespit Arşivi** sekmesi.
- Model → tür hiyerarşili üç durumlu seçim; son 1 saat/24 saat/7 gün/tümü/
  özel zaman, minimum güven, run ve gerçek görüntü varlığı filtreleri.
- Zaman, güven, alan oranı, model ve tür için çift yönlü; NULL-aware keyset
  sayfalama ve 25/50/100 satır sayfa seçenekleri.
- Katalog dışı model ve türleri kaybetmeyen salt-okunur arşiv sorgu katmanı.
- Page ve facet sayımlarını aynı revision/transaction'da alan, latest-pending
  birleştirmeli `ArchiveFetcher`.
- Tk'den bağımsız üç durumlu seçim, filtre ve cursor geçmişi state modelleri.
- Düzeltilmiş teknik sözleşmeleri ve performans sınırını kaydeden
  `TESPIT_ARSIVI_PLANI.md`.

### Değiştirildi

- Oturum Günlüğü ve Tespit Arşivi, görüntüleyiciyi oluşturan ve yakın tarihli
  kayıt retry durumunu kuran ortak snapshot controller'ını kullanıyor.
- Uygulama polling/kapanış zinciri arşiv fetcher'ını UI'ı bekletmeden,
  iki aşamalı ve idempotent biçimde yönetiyor.
- Terminal çalışma olaylarında arşiv önce hızlı yenileniyor; ardından
  `PostgresSink` commit checkpoint'i ve `MediaRecorder` drain checkpoint'i
  tamamlanınca gelen `archive_ready` olayıyla kesin yenileme yapılıyor.
- İstanbul saat dilimi verisi bulunmayan temiz Windows kurulumları için UTC+3
  geri dönüşü ve bayat snapshot sonucunda capture kimliği koruması eklendi.
- Uygulama sürümü v1.2.2'ye yükseltildi; veri modeli değişmediği için
  PostgreSQL şema sürümü 3 olarak kaldı.

### Doğrulama

- 210 otomatik test geçti.
- ASC/DESC, eşit değer ve NULL-fazı keyset senaryoları birim testlerle
  doğrulandı.
- Tree/refresh nesilleri, latest pending birleştirme, geçici bağlantı retry'ı
  ve kapanış yarışları test edildi.
- Canlı PostgreSQL 17 üzerinde arşiv ağacı, filtreli sayfa, facet sayımları
  ve gerçek medya varlığı sorguları doğrulandı.

## [1.2.1] - 2026-07-24

### Eklendi

- Dört aktif model ve 20 bilinen tespit türü için referans katalogları.
- Bilinmeyen model/sınıfları kaybetmeden genişleyen `detection_types`
  çalışma zamanı sözlüğü.
- Model/tür envanteri, zaman sayımları ve katalog dışı türler için hazır
  PostgreSQL görünümleri.
- Transaction/advisory-lock korumalı bağımsız DB şema 3 migration ve v1.2.0
  uyumluluk geri dönüş dosyası.

### Değiştirildi

- PostgreSQL şema sürümü 3'e yükseltildi; `detected_objects.type_id`,
  `(type_id, ts)` indeksi ve metin alanlarıyla tutarlılığı zorlayan birleşik
  yabancı anahtar eklendi.
- DB şema 3 migration kayıpsız ve eklemeli yapıldı; mevcut `confidence`, `bbox`,
  `area_ratio`, `model_id` ve `class_name` değerleri yerinde korunuyor.

### Doğrulama

- 138 otomatik test geçti.
- Temiz kurulum, v2→v3 migration, tekrar çalıştırma ve uyumluluk geri dönüşü
  PostgreSQL 17 üzerinde doğrulandı.
- Canlı Docker veritabanındaki 40 tespit satırının migration öncesi/sonrası
  veri parmak izi aynı kaldı.

## [1.2.0] - 2026-07-23

### Eklendi

- Oturum Günlüğü'nde görüntüsü bulunan tespitleri gösteren dar 📷 sütunu.
- Satıra çift tıklama veya **Görüntüyü Aç** düğmesiyle açılan, işaretli ve
  orijinal JPEG arasında geçiş yapabilen tekil görüntüleyici pencere.
- Salt-okunur, tek worker thread'li `SnapshotFetcher`, nesil tabanlı bayat
  sonuç filtresi ve 16 capture'lık LRU önbellek.
- Capture blob ve model özetlerini tek UUID üzerinden okuyan `fetch_capture`
  DB API'si.
- Henüz yazılmamış yeni capture için bir defalık gecikmeli yenileme ve
  görüntüyü diske kaydetme desteği.

### Değiştirildi

- NumPy, OpenCV, Pillow, PyTorch ve Ultralytics bağımlılıklarına uyumlu alt/üst
  sürüm sınırları eklendi.
- `.pt` model ağırlıkları ve kaynak `.zip` paketi Git LFS yönetimine taşındı.

### Doğrulama

- Temiz Python 3.11 sanal ortamında `pip check` hatasız tamamlandı.
- 136 otomatik test geçti.
- Semantic model iki farklı güven eşiğiyle gerçek ağırlık üzerinde çalıştırıldı.
- Git LFS pull sonrasında bütün `SHA256SUMS.txt` kayıtları doğrulandı.

## [1.1.0] - 2026-07-23

### Eklendi

- Yapılandırılmış `LogRecord`, genişletilebilir `LogSink` sözleşmesi ve asenkron `EventJournal` günlük katmanı.
- JSONL dosya kaydı, 5 MB boyut tabanlı rotasyon, konsol hedefi ve yazılabilir dizin fallback'i.
- Aynı modelin art arda değişmeyen tespitlerini bastıran; değişim, heartbeat ve çalışma sonu özeti üreten `DetectionSuppressor`.
- UI'da **Canlı Önizleme** yanında açılan **Oturum Günlüğü** sekmesi.
- Saat, seviye, kategori, run, model, mesaj ve payload ayrıntılarını canlı gösteren, renk kodlu log tablosu.
- Journal yazıcı thread'i ile Tk ana thread'i arasında sınırlı ve thread-safe `SessionLogSink`.
- Günlük altyapısı, tekrar bastırma, hata izolasyonu, kuyruk taşması ve UI canlı aktarımı için birim testleri.
- PostgreSQL 17 için sürüm-kapılı migration altyapısı, asenkron günlük sink'i,
  Docker Compose kurulumu ve JSONL backfill aracı.
- Tespitlerin sınıf, doğruluk, bbox/semantic alan bilgileriyle
  `detection_events` ve `detected_objects` tablolarına açılması.
- Orijinal/işaretli tespit karelerini içerik-adresli JPEG blob'larıyla saklayan
  `MediaRecorder`, `SnapshotGate` ve `DbMediaSink`.
- Capture/model korelasyonu, otomatik süre/boyut kotası, dry-run temizlik ve
  JPEG dışa aktarma araçları.
- Medya encoder, gate, kuyruk, migration, idempotency, engine korelasyonu ve
  gerçek PostgreSQL senaryoları için genişletilmiş test kapsamı.

### Değiştirildi

- `ProcessingEngine` uygulama olaylarını ve model tespitlerini enjekte edilen journal'a bildiriyor.
- UI kapanışı, Tk penceresi yok edilmeden önce journal kuyruğunu boşaltıp sink'leri serbest bırakıyor.
- Engine, kaynak sequence/duvar zamanını koruyan `FramePacket` taşıyor ve
  medya recorder yaşam döngüsünün tek sahibi olarak güvenli drain/release
  sırasını yönetiyor.
- Journal tekrar imzası sınıf kompozisyonuna, medya imzası kuantize
  bbox/semantic footprint'e yükseltildi.

### Teknik notlar

- Kalıcı kayıt yolu varsayılan olarak `~/.cache/roadvision/logs/roadvision.jsonl`.
- Üretici taraf disk I/O'sunu beklemez; dolu kuyrukta düşen kayıt sayısı sonraki kayda eklenir.
- Oturum günlüğü görünümü son 1.000 satırla, UI kuyruğu son 2.000 kayıtla sınırlıdır.
- Medya kuyruğu hem iş adedi hem yaklaşık ndarray belleğiyle sınırlıdır;
  bağlantı ve medya hatları inference akışını durdurmadan best-effort çalışır.

## [1.0.1] - 2026-07-22

### Düzeltildi

- Çalıştırmaların yaşam döngüsü birbirinden ayrıldı; önceki worker'lar tamamlanmadan yeni çalışma başlatılması engellendi.
- Durdurma ve uygulama kapanışı, arayüzü bekletmeden güvenli worker tamamlanmasını izleyecek şekilde düzenlendi.
- Kaynak türü, dosya veya kamera değiştiğinde etkin çalışma durduruluyor; bekleyen eski görüntü temizleniyor ve düğme **Başlat** durumuna sıfırlanıyor.
- Geciken eski çalışma olaylarının yeni önizleme ve durum bilgisini değiştirmesi engellendi.

## [1.0.0] - 2026-07-22

### Eklendi

- Kamera, fotoğraf ve video kaynaklarında birden fazla YOLO modelini çalıştıran ilk kararlı masaüstü sürümü.
- Model bazlı güven eşiği, Box/Maske görünürlüğü ve `models.json` tabanlı kaydırılabilir model listesi.
