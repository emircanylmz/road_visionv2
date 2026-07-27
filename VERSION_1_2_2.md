# RoadVision v1.2.2

Yayın tarihi: **27 Temmuz 2026**

v1.2.2, PostgreSQL'e kalıcı yazılmış tespitleri uygulamadan ayrılmadan
incelemek için **Tespit Arşivi** sekmesini ekler. Uygulama sürümü v1.2.2,
veritabanı şema sürümü 3'tür; yeni tablo veya veri migration'ı gerekmez.

## Kullanıcıya görünen yenilikler

- Model → tespit türü hiyerarşili, üç durumlu filtre ağacı
- Son 1 saat, 24 saat, 7 gün, tümü ve özel tarih aralığı
- Minimum güven, run ve yalnız gerçek görüntüsü olan kayıt filtreleri
- Zaman, güven, alan oranı, model ve tür kolonlarında çift yönlü sıralama
- 25/50/100 satırlık keyset tabanlı ileri/geri sayfalama
- Mevcut filtrelerde tür bazında tespit sayımları
- 📷 işaretli geçmiş satırdan mevcut işaretli/orijinal görüntüleyiciye geçiş
- DSN yok, DB kapalı, eski şema, boş sonuç ve hata durumlarında açıklayıcı
  sekme durumu

## Teknik güvenilirlik

- UI, inference ve journal thread'leri PostgreSQL okumasını beklemez.
- Tür ağacı ve sonuç yenilemesi birbirinden bağımsız generation taşır.
- Page ve tür sayımları tek refresh revision'ında ve aynı salt-okunur
  transaction'da alınır.
- Bekleyen filtre değişiklikleri sınırsız kuyruk oluşturmaz; yalnız en güncel
  refresh tutulur.
- ASC/DESC ve NULL confidence/area değerleri için kayıpsız keyset cursor
  sözleşmesi kullanılır.
- “Görüntülü” filtresi yalnız UUID varlığını değil, yaşayan
  `media_captures` satırını doğrular.
- Arşiv fetcher'ı kapanırken Tk ana thread'ini çalışan DB sorgusunda
  bekletmez.
- Başarısız bir sıralama isteği eski satırların başlığını değiştirmez; ok
  yalnız yeni sayfa başarıyla uygulandığında güncellenir.
- Pencere geri getirildiğinde sekmenin lazy yenilemesi yeniden etkinleşir;
  temiz Windows kurulumunda IANA saat dilimi yoksa İstanbul için UTC+3
  fallback kullanılır.
- Bir çalışma bittiğinde arşivin kesin yenilemesi sabit süre tahminine değil,
  journal PostgreSQL commit'i ile medya kuyruğu drain checkpoint'lerinin
  ikisinin de tamamlanmasına bağlıdır.

Doğrulama paketi 210 otomatik testten oluşur. Ayrıca çalışan PostgreSQL 17
verisinde beş kolonun iki yönündeki 10 keyset yürüyüşü, gerçek medya filtresi
ve asenkron worker yaşam döngüsü uçtan uca çalıştırılmıştır.

## Bilinçli sınırlar

- Arşiv tekil fact satırlarını gösterir; aynı event içindeki birden fazla
  nesne ayrı satırdır.
- Journal tekrar bastırma nedeniyle bu görünüm her video karesinin ham dökümü
  değildir.
- Keyset büyük OFFSET maliyetini önler; confidence, alan veya görünen ad
  sıralamalarının milyonlarca satırda sabit süre garantisi yoktur. Böyle bir
  SLA gerekirse ölçüme dayalı indeksler ayrı DB migration'ına alınacaktır.
- CSV/JSON dışa aktarma v1.4 kapsamındadır.

Tasarım ve yarış sözleşmeleri için
[TESPIT_ARSIVI_PLANI.md](TESPIT_ARSIVI_PLANI.md), PostgreSQL kurulumu için
[DATABASE.md](DATABASE.md) dosyasına bakın.
