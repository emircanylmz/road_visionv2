# Değişiklik günlüğü

## [Yayımlanmadı]

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
