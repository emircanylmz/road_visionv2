# Değişiklik günlüğü

## [Yayımlanmadı] - 2026-07-22

### Eklendi

- Yapılandırılmış `LogRecord`, genişletilebilir `LogSink` sözleşmesi ve asenkron `EventJournal` günlük katmanı.
- JSONL dosya kaydı, 5 MB boyut tabanlı rotasyon, konsol hedefi ve yazılabilir dizin fallback'i.
- Aynı modelin art arda değişmeyen tespitlerini bastıran; değişim, heartbeat ve çalışma sonu özeti üreten `DetectionSuppressor`.
- UI'da **Canlı Önizleme** yanında açılan **Oturum Günlüğü** sekmesi.
- Saat, seviye, kategori, run, model, mesaj ve payload ayrıntılarını canlı gösteren, renk kodlu log tablosu.
- Journal yazıcı thread'i ile Tk ana thread'i arasında sınırlı ve thread-safe `SessionLogSink`.
- Günlük altyapısı, tekrar bastırma, hata izolasyonu, kuyruk taşması ve UI canlı aktarımı için birim testleri.

### Değiştirildi

- `ProcessingEngine` uygulama olaylarını ve model tespitlerini enjekte edilen journal'a bildiriyor.
- UI kapanışı, Tk penceresi yok edilmeden önce journal kuyruğunu boşaltıp sink'leri serbest bırakıyor.

### Teknik notlar

- Kalıcı kayıt yolu varsayılan olarak `~/.cache/roadvision/logs/roadvision.jsonl`.
- Üretici taraf disk I/O'sunu beklemez; dolu kuyrukta düşen kayıt sayısı sonraki kayda eklenir.
- Oturum günlüğü görünümü son 1.000 satırla, UI kuyruğu son 2.000 kayıtla sınırlıdır.

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
