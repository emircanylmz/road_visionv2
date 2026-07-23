# RoadVision v1.2.0

Yayın tarihi: **23 Temmuz 2026**

Bu sürüm, v1.1.0 PostgreSQL medya arşivini Oturum Günlüğü'ne bağlar. Görüntüsü
bulunan tespit satırları artık veritabanındaki işaretli ve orijinal JPEG
çiftini uygulama içinden açabilir.

## Öne çıkanlar

- Oturum Günlüğü'nde capture varlığını gösteren 📷 sütunu
- Çift tıklama ve **Görüntüyü Aç** düğmesi
- İşaretli/orijinal görünüm, yenileme ve diske kaydetme
- Tk ana thread'ini bekletmeyen salt-okunur PostgreSQL worker'ı
- Hızlı ardışık seçimlerde bayat sonucu engelleyen nesil kimliği
- 16 capture'lık LRU önbellek
- Yeni kayıt yarışı için bir defalık 1,5 saniyelik otomatik yenileme
- NumPy 1.x ile uyumlu temiz kurulum bağımlılık sınırları
- Model ağırlıkları ve kaynak paket için Git LFS geçmiş dönüşümü

## Hata izolasyonu

`ROADVISION_DB_DSN` tanımlı değilse görüntüleyici kurulmaz ve uygulamanın
diğer işlevleri değişmeden çalışır. PostgreSQL bağlantısı kesilirse hata
yalnız görüntüleyici penceresinde gösterilir; sonraki istek yeni bağlantı
kurmayı dener.

## Doğrulama

- Temiz Python 3.11 sanal ortamında hatasız `pip check`
- 136 otomatik test
- Gerçek semantic ağırlıkla iki güven eşiğinde duman testi
- Altı Git LFS nesnesi için tam SHA-256 doğrulaması

Ayrıntılı kullanım için [LOGGING.md](LOGGING.md), PostgreSQL kurulumu ve
saklama yapısı için [DATABASE.md](DATABASE.md) dosyasına bakın.
