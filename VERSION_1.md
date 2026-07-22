# RoadVision v1.0.0

Yayın tarihi: **22 Temmuz 2026**

Bu belge, RoadVision masaüstü uygulamasının ilk kararlı sürümünün kapsamını, çalışma biçimini ve teslim içeriğini açıklar.

## Sürüm özeti

RoadVision v1.0.0; yol görüntülerini kamera, fotoğraf veya video kaynağından alıp birden fazla YOLO modelini aynı kare üzerinde çalıştıran Tkinter tabanlı bir test arayüzüdür. Model kataloğu koddan bağımsız olarak `models.json` dosyasından yüklenir.

İlk sürümle birlikte gelen model görevleri:

- Yol çizgisi semantik segmentasyonu
- Trafik tabelası ve trafik ışığı tespiti
- Çukur tespiti
- Yol işareti hasarı tespiti

## Temel özellikler

- Kamera, fotoğraf ve video kaynağı desteği
- Aynı kare üzerinde bir veya birden fazla model çalıştırma
- Çalışma sırasında model seçimini değiştirme
- Her model için bağımsız güven eşiği
- Her model için bağımsız **Box göster** veya **Maske göster** ayarı
- Çizim kapalıyken çıkarım ve nesne sayımına devam etme
- `models.json` içeriğinden otomatik oluşturulan kaydırılabilir model listesi
- Hızlı, Dengeli ve Kalite performans profilleri
- CPU üzerinde paralel model çıkarımı
- CUDA, Apple MPS ve gerekli durumlarda CPU uyumluluk modu
- Kaynak değiştiğinde eski görüntüyü temizleme ve Başlat düğmesini sıfırlama
- En güncel kareyi koruyan düşük gecikmeli işleme kuyruğu

## Kurulum

Python 3.11 önerilir.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Ana bağımlılıklar NumPy, OpenCV, Pillow, PyTorch ve Ultralytics'tir. Kamera kullanımı işletim sistemi kamera izni gerektirebilir.

## Kullanım

1. Kamera, fotoğraf veya video kaynağını seçin.
2. Kaydırılabilir listeden en az bir modeli etkinleştirin.
3. İlgili modellerin güven eşiklerini ayarlayın.
4. Görüntülenmesini istemediğiniz Box veya Maskeyi kapatın. Model arka planda çalışmaya devam eder.
5. **Başlat** düğmesine basın.

Kaynak değiştirildiğinde devam eden işlem otomatik durdurulur, önizleme temizlenir ve arayüz yeni çalıştırma için hazırlanır.

## Yeni model ekleme

Yeni bir model için ağırlık dosyasını projeye ekleyip `models.json` içindeki `models` listesine yeni bir kayıt eklemek yeterlidir. Uygulama yeniden açıldığında etkin kayıt model listesinin altında görünür.

Her model kaydı şu alanları içermelidir:

- `id`: benzersiz model kimliği
- `display_name`: arayüzde gösterilen ad
- `short_name`: kısa model adı
- `task`: Ultralytics görev tipi
- `weights`: ağırlık dosyasının yolu
- `input_size`: özgün giriş boyutu
- `color_bgr`: çizim rengi
- `enabled`: arayüzde kullanılabilirlik durumu

## Proje yapısı

```text
app.py                         Uygulama giriş noktası
models.json                    Dinamik model kataloğu
roadvision/ui/app.py           Tkinter arayüzü
roadvision/engine.py           Capture ve inference yaşam döngüsü
roadvision/sources.py          Kamera, fotoğraf ve video kaynakları
roadvision/models/manager.py   Model cache, ayarlar ve çoklu çalıştırma
roadvision/models/yolo.py      YOLO tahmin ve çizim adaptörü
tests/                         Birim testleri
01_... / 04_...                Model ağırlıkları, metrikler ve model kartları
```

Mimari ayrıntılar [ARCHITECTURE.md](ARCHITECTURE.md), model envanteri ise [MODEL_INVENTORY.csv](MODEL_INVENTORY.csv) dosyasındadır.

## Doğrulama

Test paketi şu komutla çalıştırılır:

```bash
python3 -m unittest discover -s tests -v
```

Ultralytics ve PyTorch dahil `requirements.txt` bağımlılıklarının test ortamında kurulu olması gerekir.

## v1.0.0 teslim içeriği

- Uygulama kaynak kodu
- Dört başlangıç modelinin ağırlıkları ve doğrulama metrikleri
- Model kataloğu ve envanteri
- Mimari ve kullanım belgeleri
- Kamera, kaynak, engine, model yapılandırması ve çizim davranışı testleri

Bu sürüm, ilk kararlı Git etiketi olarak `v1.0.0` adıyla yayımlanmak üzere hazırlanmıştır.
