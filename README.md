# RoadVision — çoklu model test arayüzü

**Kararlı sürüm:** v1.1.0 — 23 Temmuz 2026

İlk kararlı teslimin kapsamı için [VERSION_1.md](VERSION_1.md), bu sürümün
özeti için [VERSION_1_1.md](VERSION_1_1.md), tüm değişiklikler için
[CHANGELOG.md](CHANGELOG.md) dosyasına bakın. Günlük altyapısı
[LOGGING.md](LOGGING.md), PostgreSQL ve medya kaydı [DATABASE.md](DATABASE.md)
belgelerinde açıklanır.

Bu proje, `models.json` kataloğundaki YOLO modellerini kamera, fotoğraf veya video üzerinde tek tek ya da birlikte çalıştıran modüler bir masaüstü arayüzüdür. İşlem başladıktan sonra model seçimi ve modele özel güven eşiği değiştirilebilir; fotoğraflarda son kare otomatik olarak yeniden işlenir, kamera/video akışında yeni seçim takip eden karelere uygulanır.

## Hızlı başlangıç

Python 3.11 önerilir. macOS ve çoğu Python.org kurulumunda Tkinter Python ile birlikte gelir.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

## JSON model kataloğu

Uygulamadaki modeller [models.json](models.json) dosyasından yüklenir. Model ağırlığını güncellemek için Python kodunu değiştirmek gerekmez; ilgili `weights` değerini değiştirip uygulamayı yeniden başlatmak yeterlidir.

```json
{
  "schema_version": 1,
  "models": [
    {
      "id": "pothole",
      "display_name": "Çukur ve Rögar Kapağı Tespiti",
      "short_name": "Çukur / Rögar",
      "task": "detect",
      "weights": "03_pothole_detection/pothole_manhole_yolo26s_768_v1/pothole_manhole_yolo26s_768_v1.pt",
      "input_size": 768,
      "color_bgr": [60, 80, 245],
      "enabled": true
    }
  ]
}
```

Alanlar:

- `id`: uygulama içinde benzersiz, değişmeyen model kimliği.
- `display_name`: model seçim kartında gösterilen tam ad.
- `short_name`: bounding box üzerinde kullanılan kısa ad.
- `task`: Ultralytics task değeri (`detect`, bu projedeki semantic model için `semantic` vb.).
- `weights`: JSON dosyasının klasörüne göre relatif veya mutlak `.pt` yolu.
- `input_size`: kalite profilindeki özgün inference boyutu.
- `color_bgr`: overlay için 0–255 aralığında BGR renk dizisi.
- `enabled`: `false` yapılırsa model kod değiştirmeden arayüzden kaldırılır.

Farklı bir katalog dosyası da kullanılabilir:

```bash
ROADVISION_MODEL_CONFIG=/tam/yol/yeni-modeller.json python3 app.py
```

JSON şeması, zorunlu alanlar, renk değerleri, tekrar eden kimlikler ve giriş boyutları açılışta doğrulanır. Ağırlık dosyasının varlığı model seçilip başlatıldığında kontrol edilir.

Arayüzde:

1. Kamera, fotoğraf veya video kaynağını seçin.
2. Model listesinden en az bir modeli işaretleyin.
3. Her model için güven eşiğini ve Box/Maske görünürlüğünü ayarlayın, ardından **Başlat** düğmesine basın.
4. İşlem sürerken model kutularını açıp kapatabilirsiniz.
5. Sağ taraftaki **Oturum Günlüğü** sekmesinden uygulama olaylarını ve model tespitlerini anlık izleyebilirsiniz.

## Olay ve tespit günlüğü

Uygulama açılış, çalışma, durum, hata, kapanış ve model tespitlerini yapılandırılmış kayıtlar halinde tutar. Sağ paneldeki **Oturum Günlüğü** sekmesi bu oturumun kayıtlarını saat, seviye, kategori, run, model, mesaj ve ayrıntı alanlarıyla canlı gösterir. **Ekranı Temizle** yalnız tablodaki satırları kaldırır; diskteki kayıtları silmez.

Kalıcı kayıtlar JSONL biçiminde aşağıdaki dosyaya yazılır:

```text
~/.cache/roadvision/logs/roadvision.jsonl
```

Dosya 5 MB'a ulaştığında önceki içerik `roadvision.jsonl.1` olarak döndürülür. Disk yazımı inference ve UI thread'lerini bekletmez. Art arda aynı modelden aynı sayıda tespit gelirse tekrarlar bastırılır; değişiklikler ve uzun süren serilerin özetleri kaydedilir. Ayrıntılı kayıt şeması, yaşam döngüsü ve genişletme noktaları için [LOGGING.md](LOGGING.md) dosyasına bakın.

Her modelin güven eşiği bağımsızdır. **Box göster** veya **Maske göster** kapatıldığında model arka planda tespit yapmaya ve istatistik üretmeye devam eder; yalnızca ilgili çizim önizlemede gösterilmez.

v1.0.1'de kaynak türü, dosya veya kamera seçimi değiştirildiğinde etkin çalışma güvenli biçimde durdurulur; eski önizleme temizlenir ve düğme **Başlat** durumuna sıfırlanır. Durdurma tamamlanmadan yeni çalışma başlatılmaz.

Model listesi `models.json` içindeki etkin kayıtlardan oluşturulur ve dikey olarak kaydırılabilir. Dosyaya eklenen yeni bir model, uygulama yeniden açıldığında listenin altında otomatik görünür.

Performans profilleri çalışma sırasında değiştirilebilir:

- **Hızlı:** yol çizgisi en fazla 640 px, diğer modeller en fazla 512 px giriş kullanır.
- **Dengeli:** yol çizgisi en fazla 768 px, diğer modeller en fazla 640 px kullanır.
- **Kalite (varsayılan):** modeller eğitimde seçilen özgün 1024/768/640 px giriş boyutlarını kullanır.

Daha düşük giriş boyutu FPS'i yükseltir ve ms değerini düşürür; küçük veya uzaktaki nesnelerde bir miktar doğruluk kaybı oluşturabilir.

Telefon fotoğrafları yüklenirken EXIF Orientation piksel verisine uygulanır. Bounding box, label ve maskeler doğrudan Ultralytics `Results.plot()` hattıyla çizilir; letterbox/ölçek geri dönüşü model kütüphanesinin kendi koordinat sistemi içinde yapılır.

Kamera izni ilk çalıştırmada işletim sistemi tarafından sorulabilir. Hiç kamera bulunmazsa fotoğraf ve video kaynakları kullanılmaya devam edebilir.

macOS AVFoundation kamera indekslerini ardışık sunduğu için tarama ilk boş indekste durur. Bu, var olmayan `1..7` indeksleri için OpenCV'nin terminale bastığı `out device of bound` uyarılarını engeller.

## Modüler yapı

```text
app.py
└── RoadVisionApp                 # Yalnızca Tk ana thread'inde UI günceller
    ├── EventJournal              # JSONL + PostgreSQL + canlı oturum günlüğü
    └── ProcessingEngine          # Worker yaşam döngüsü ve latest-frame kuyruğu
        ├── MediaRecorder         # Sınırlı async JPEG + MediaSink
        ├── MediaSource
        │   ├── CameraSource ── Camera
        │   ├── ImageSource
        │   └── VideoSource
        └── ModelManager
            ├── ModelRegistry
            └── YoloModelAdapter  # Lazy load, predict, annotate, release
```

Ayrıntılı sınıf sorumlulukları ve genişletme noktaları [ARCHITECTURE.md](ARCHITECTURE.md) dosyasındadır.

## Thread ve performans yaklaşımı

- Kamera/video yakalama, model çıkarımından ayrı bir worker thread'inde çalışır.
- Boyutu 1 olan kuyruk sadece en güncel kareyi tutar. Model akıştan yavaşsa eski kareler birikerek gecikme oluşturmaz.
- CUDA/MPS üzerindeki model çağrıları tek inference worker'ında sıralı çalışır. Böylece GPU belleği ve kernel'ler için dört modelin aynı anda yarışması engellenir.
- CPU aygıtında birden fazla model seçildiğinde bağımsız model tahminleri kalıcı worker havuzunda paralel çalışır. PyTorch intra-op thread sayısı seçili model sayısına göre sınırlandırılarak oversubscription engellenir; çizimler tahminler bittikten sonra deterministik sırayla birleştirilir.
- Modeller ilk seçildiklerinde yüklenir ve bellekte önbelleğe alınır; daha sonraki model geçişleri hızlıdır.
- CUDA bulunduğunda otomatik olarak `cuda:0`, Apple MPS bulunduğunda `mps`, aksi halde `cpu` kullanılır. CUDA üzerinde yarım hassasiyet açılır.
- Apple MPS üzerinde henüz uygulanmamış `torchvision::nms` operatörü için CPU fallback otomatik etkinleştirilir. Kullanılan PyTorch sürümü bu fallback'i uygulamazsa ilk hatalı çağrı CPU'da yeniden denenir ve model cache'i CPU moduna geçirilir.
- Bazı macOS torch/torchvision sürümlerinde MPS NMS fallback'i detection kutularının `xyxy` koordinatlarını bozabildiği için `detect`, instance `segment`, `obb` ve `pose` görevleri doğrudan CPU uyumluluk modunda çalışır. Saf `semantic` yol çizgisi modeli MPS üzerinde kalır; arayüz bunu `MPS + CPU(DET)` olarak gösterir.
- Video kaynakları kendi FPS değerine göre beslenir; arayüz gerçek zamanlı kalırken inference yetişemezse kare düşürür.
- Worker callback'leri UI'ı doğrudan değiştirmez. Olaylar thread-safe UI kuyruğundan Tk ana thread'ine aktarılır.
- Arayüzdeki FPS, son bir saniyedeki kare sayımı yerine üstel yumuşatılmış gerçek inference süresinden hesaplanır. `Toplam ms` bütün seçili modellerin duvar saati süresidir; model yanındaki ms ise o modelin kendi tahmin süresidir.

## Test

```bash
python3 -m unittest discover -s tests -v
```

Testler kamera yaşam döngüsünü, Unicode fotoğraf yolunu, image source davranışını, en az bir model kuralını ve çalışan akışta dinamik model değişimini kapsar.

Günlük/veritabanı/medya testleri; JSONL yazımı ve rotasyonu, seviye filtresi,
kuyruk taşması, sink hata izolasyonu, tekrar bastırma, PostgreSQL migration,
görüntü kapısı, JPEG tekilleştirme ve capture korelasyonunu da doğrular.

Docker PostgreSQL kurulumu, pgAdmin sorguları, görüntü dışa aktarma ve saklama
kotası için [DATABASE.md](DATABASE.md); gözden geçirilmiş tasarım kararları
için [MEDYA_TASARIM_PLANI.md](MEDYA_TASARIM_PLANI.md) dosyasına bakın.

## Sürüm geçmişi ve geri dönüş

Kararlı sürümler Git etiketleriyle, devam eden çalışma ise ayrı dallarla
korunur. v1.1.0 geliştirmesi `release/v1.1.0` dalındadır; mevcut
`release/v1.0.1`, `v1.0.1` ve `v1.0.0` geçmişi değiştirilmez.

Eski bir sürümü yalnız incelemek veya çalıştırmak için:

```bash
git fetch --all --tags
git switch --detach v1.0.1
```

Eski sürüm üzerinde yeni değişiklik yapmak isterseniz etiketten ayrı bir dal oluşturun:

```bash
git switch -c inceleme/v1.0.1 v1.0.1
```

Güncel sürüm dalına dönmek için `git switch release/v1.1.0` kullanılabilir.

## Model klasörleri

## Klasorler

- `01_roadline_semantic`: tek sinifli yol cizgisi semantic segmentation modeli, 1024 giris.
- `02_tabela_detection`: 16 sinifli tabela ve trafik isigi detection modeli, 640 giris.
- `03_pothole_detection`: iki sinifli (`pothole`, `manhole_cover`) aktif detection modeli ve korunmuş eski tek sınıflı çukur modeli, 768 giriş.
- `04_road_marking_damage`: tek sinifli yol isareti hasari detection modeli, 640 giris.

Çukur/rögar klasöründe eski tek sınıflı `model.pt` korunur. Aktif iki sınıflı model, kendi eğitim ve test artefaktlarıyla `pothole_manhole_yolo26s_768_v1/` altındadır. Diğer model klasörlerinde seçilen ağırlık `model.pt`, test metrikleri `metrics.json` ve final grafikler `test_results/` altında tutulur.

`MODEL_INVENTORY.csv` dört aktif modelin ortak envanteridir. Her model klasöründeki `SHA256SUMS.txt`, ilgili ağırlık dosyalarının yerel SHA-256 değerlerini verir.

Tüm model ağırlıkları uygulamanın `YoloModelAdapter` sınıfı üzerinden gerçek bir sentetik kare ile yükleme ve inference bakımından doğrulanmıştır.
