# Değişiklik günlüğü

## [Yayımlanmadı]

### Eklendi

- Web paneli Faz 2 (RVU-0004): `(ts,id)` keyset imleçli, seviye/kategori/
  model/run/zaman filtreli `/api/logs` + `/api/logs/{id}` + `/api/meta/models`
  uçları ve DB'siz test edilen saf `logquery` üreticisi; masaüstü v2 tasarım
  token'larını taşıyan React+TypeScript SPA (giriş/kayıt, ikon-raylı kabuk,
  payload çekmeceli Loglar sayfası, Üyeler/Oturumlar/Denetim sekmeli Yönetim
  sayfası); SPA'yı sunup `/api`'yi proxy'leyen nginx compose servisi
  (127.0.0.1:8080) ve api imajında `--proxy-headers`; 100k kayıt kabulünü
  seed/ölçüm/temizlikle kanıtlayan `verify_faz2.py`. Masaüstü kodu ve
  `public` şeması değişmedi (`--seed` yalnız isteğe bağlı ve sahip DSN'iyle
  sentetik `faz2-seed` kayıtları ekler).
- Web paneli Faz 1 (RVU-0004): webapp şema v1 (`users`, `sessions`,
  `admin_audit`), Argon2id + zamanlama-eşitlemeli giriş, oturuma bağlı
  double-submit CSRF (`X-RoadVision-CSRF`), IP+e-posta bazlı giriş oran
  sınırı, yönetici onay/ret/devre dışı akışı (durum geçişi UPDATE..WHERE
  ile yarış-güvenli, oturum iptali ve audit ile tek transaction),
  `/api/auth/*` ve `/api/admin/*` uçları, tek biçim hata gövdesi
  `{"error":{code,message}}`, `create_admin.py` ve HTTP kabul betiği
  `verify_faz1.py`; web birim testleri (argon2 yoksa özet testleri zarifçe
  atlanır). Masaüstü kodu ve `public` şeması değişmedi.
- Web paneli Faz 0 (RVU-0004, bkz. [WEB_PLANI.md](WEB_PLANI.md)): salt-okunur
  `roadvision_web` DB rolü ve `webapp` şeması için tekrar çalıştırılabilir
  bootstrap (`web/db/bootstrap.sql` + compose initdb kancası), masaüstü
  `ensure_schema` deseninde sürüm-kapılı web migration runner'ı
  (`web/app/migrations.py`, advisory lock 1385428467), FastAPI iskeleti ve
  `/healthz` ucu, compose `api` servisi (127.0.0.1:8800), Faz 0 kabulünü
  makinede kanıtlayan `web/scripts/verify_foundation.py` ve psycopg'siz
  çalışan migration birim testleri. Masaüstü kodu ve `public` şeması
  değişmedi.

### Düzeltildi

- Faz 1 kabul verileri `EmailStr` tarafından reddedilen ayrılmış
  `.invalid`/`.local` alanlarından geçerli alanlara taşındı;
  `create_admin.py` giriş yapılamayan hesap üretmemek için e-postayı
  doğrulayıp normalize ediyor ve kayıt API'si yalnız boşluktan oluşan
  görünen adları reddediyor. İlk yönetici ile API kaydı aynı 10–200
  karakter parola sınırını kullanıyor.
- Yönetici aktif oturum listesi mutlak sürenin yanında yapılandırılmış
  hareketsizlik süresini de uygular.
- Web bootstrap parola aktarımı `psql \gset` ile sessizleştirildi; rol
  parolası terminal veya CI çıktısına yazılmıyor. Bu davranış regresyon
  testiyle korunuyor.
- Faz 2 log sayfalaması bir fazla satır okuyarak tam dolu son sayfada
  sahte `next_cursor` üretmiyor; bozuk/naif zamanlı imleçler ve UTC ofsetsiz
  zaman filtreleri reddediliyor. Model kataloğu yokluğu transaction'ı hata
  durumuna düşürmeden `to_regclass` ile belirleniyor.
- Faz 2 kabul betiğinin PostgreSQL modulo ifadeleri psycopg yer tutucu
  ayrıştırmasına uygun hale getirildi; p95 hesabı en yakın-rank yöntemiyle
  yapılıyor, ağ/seed hataları kontrollü FAIL üretiyor ve kabul oturumu
  çıkışta kapatılıyor.
- React Router'ın birbiriyle çakışan güvenlik duyurularına maruz sürümleri
  yerine küçük bir History API yönlendiricisi kullanıldı. Eksik
  `package-lock.json` üretildi; üretim bağımlılığı denetimi sıfır açıkla
  tamamlandı.
- nginx için CSP, frame, MIME-sniffing, referrer ve cross-origin opener
  başlıkları, kapalı sürüm imzası ve 1 MiB istek gövdesi sınırı eklendi.

### Doğrulama

- Web birim testleri 38/38, masaüstü regresyon testleri 255/255 geçti.
- PostgreSQL şemaları `webapp=1` ve `public=3` olarak doğrulandı; Faz 1
  HTTP kabul betiği kayıt/onay, CSRF, audit, rol ve oturum iptal akışlarını
  çalışan API üzerinde PASS sonucu ile tamamladı.
- Faz 2 kabulü 100.000 kayıtta 30'ar filtreli/filtresiz keyset sayfasını
  yineleme olmadan gezdi; p95 sırasıyla 9,4 ms ve 34,8 ms ölçüldü. Seed
  edilen 99.629 kayıt kabulden sonra temizlendi.
- Frontend strict TypeScript üretim derlemesi ve `npm audit --omit=dev`
  geçti (0 açık). nginx SPA/proxy/güvenlik başlıkları ile gerçek tarayıcıda
  giriş, log filtresi, payload ayrıntısı, yönetim ve çıkış akışları
  doğrulandı; tarayıcı konsolunda hata veya uyarı oluşmadı.

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
