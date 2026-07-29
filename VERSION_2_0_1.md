# RoadVision v2.0.1

**Yayın tarihi:** 29 Temmuz 2026
**Uygulama sürümü:** `v2.0.1`
**PostgreSQL şema sürümü:** `3`

## Sürüm özeti

v2.0.1, PyQt6 tabanlı RoadVision Arayüz v2 deneyimini Jetson ve Linux
sahasında daha güvenilir çalışacak kamera/model hazırlama hattıyla
birleştirir. Mevcut Tk arayüzü `ROADVISION_UI=tk` ile uyumluluk seçeneği
olarak korunur. Veritabanı şeması değişmediği için v1.2.1/v1.2.2 şema 3
verileri doğrudan kullanılabilir; veri migration'ı gerekmez.

## Öne çıkan değişiklikler

### Jetson CSI ve Linux kamera desteği

- `nvarguscamerasrc` kullanan GStreamer/NVArgus kamera kaynağı eklendi.
- `ROADVISION_CSI_SENSORS=0` veya `0,1` ile CSI sensörleri PyQt ve Tk kamera
  listelerine eklenebilir.
- `ROADVISION_CSI_FLIP_METHOD` ile Jetson görüntü yönü ayarlanabilir.
- Linux/V4L2 kameralarında çözünürlük ve FPS'ten önce MJPG formatı istenir.
  `ROADVISION_CAMERA_FOURCC` ile format değiştirilebilir veya boş değerle
  kapatılabilir.

CSI örneği:

```bash
export ROADVISION_CSI_SENSORS=0
export ROADVISION_CSI_FLIP_METHOD=0
./scripts/run_with_db.sh
```

CSI yolu, GStreamer destekli OpenCV ve Jetson üzerindeki
`nvarguscamerasrc` bileşenini gerektirir. PyPI OpenCV paketleri çoğunlukla
GStreamer içermediğinden JetPack ile uyumlu sistem OpenCV kurulumu
kullanılmalıdır.

### Model başlangıcı ve CUDA tanılama

- Başlangıçta seçili modeller ilk gerçek kare işlenmeden önce yüklenir.
- CUDA modelleri 64×64 sentetik kareyle bir defa ısındırılır.
- Yükleme veya warm-up hatasında kısmi model kaynağı serbest bırakılır.
- CUDA üzerinde `torchvision::nms` eksikse sessiz CPU dönüşü yerine JetPack
  ile uyumlu NVIDIA Torch/Torchvision kurulumu için açıklayıcı hata verilir.
- MPS uyumluluk ve CPU fallback davranışı değiştirilmemiştir.

### Arşiv ve veritabanı kullanımı

- Minimum güven sürgüsü yalnız filtre işaretlendiğinde etkinleşir.
- Run alanı iki arayüzde de **Çalışma no (Run)** olarak açıklanır.
- `scripts/run_with_db.sh`, `.venv` mevcutsa proje Python'unu otomatik seçer.
- `model_v2` ile oluşturulmuş PostgreSQL şema 3 veritabanı aynı
  `ROADVISION_DB_DSN` değeriyle doğrudan kullanılabilir.

Yerel PostgreSQL ile başlatma:

```bash
python3 -m pip install -r requirements-db.txt
./scripts/run_with_db.sh
```

`.env` gizli bağlantı bilgisi içerir ve Git tarafından izlenmez. Örnek
ayarlar için `.env.example` kullanılmalıdır.

## Uyumluluk

- Önerilen Python sürümü: 3.11
- Varsayılan arayüz: PyQt6
- Uyumluluk arayüzü: Tkinter
- Desteklenen kaynaklar: USB/V4L2 kamera, Jetson CSI, fotoğraf ve video
- Desteklenen aygıtlar: CPU, CUDA ve Apple MPS
- PostgreSQL: şema sürümü 3

## Doğrulama

- 255 otomatik test atlama olmadan geçti.
- 47 veritabanı testi `psycopg` kurulu ortamda geçti.
- PyQt6 ekran-dışı başlangıç/kapanış testi başarılı oldu.
- Dört gerçek model CPU üzerinde sentetik kareyle çıkarım yaptı.
- PostgreSQL 17.10 üzerinde arşiv, snapshot ve medya bileşenleri etkinleşti.

## Geri dönüş

v2.0.1 veritabanı şemasını değiştirmez. Uygulama geri dönüşü için önce
çalışan işlemi kapatın ve önceki etiketi açın:

```bash
git switch --detach v1.2.2
```

Mevcut PostgreSQL volume'ü korunabilir. `docker compose down -v` veritabanı
volume'ünü sileceğinden geri dönüş için kullanılmamalıdır.
