# RoadVision v1.1.0

Yayın tarihi: **23 Temmuz 2026**

Bu sürüm v1.0.1 yaşam döngüsü temelini koruyarak yapılandırılmış günlük,
PostgreSQL tespit kaydı ve tespit görüntüsü arşivini ekler. Eski sürümler
`v1.0.0` ve `v1.0.1` Git etiketlerinde değişmeden kalır.

## Öne çıkanlar

- JSONL, canlı UI ve isteğe bağlı PostgreSQL günlük hedefleri
- Sınıf, doğruluk, bbox ve semantic alan bilgilerinin sorgulanabilir kaydı
- Ham/işaretli tespit karelerinin asenkron ve içerik-adresli JPEG saklanması
- Aynı sahneyi, minimum aralığı ve run/saat kotalarını yöneten medya kapısı
- Çoklu model için kare başına tek capture UUID ve iki blob
- 30 günlük retention ve yaklaşık 2 GB blob kotasının otomatik uygulanması
- Docker Compose PostgreSQL kurulumu, backfill, prune ve JPEG export araçları
- Transaction, advisory lock ve retry-safe şema v2 migration'ı

## Güvenlik

Gerçek bağlantı bilgileri `.env` dosyasında tutulur ve Git tarafından yok
sayılır. Repository yalnız `.env.example` içindeki değiştirilebilir örnek
değerleri içerir. PostgreSQL portu varsayılan olarak yalnız
`127.0.0.1:5433` üzerinde yayınlanır.

## Doğrulama

- 124 otomatik test
- PostgreSQL 17 temiz kurulum ve v1→v2 migration testi
- Dört eşzamanlı migration başlangıcı
- Gerçek DB üzerinde idempotent iki-blob/iki-model capture ve event JOIN testi
- Dry-run ve güvenli apply medya temizliği

Kurulum ve sorgular için [DATABASE.md](DATABASE.md), tasarım kararları için
[MEDYA_TASARIM_PLANI.md](MEDYA_TASARIM_PLANI.md) dosyasına bakın.
