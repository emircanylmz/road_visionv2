# Değişiklik günlüğü

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
