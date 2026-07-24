# RoadVision v1.2.1

Yayın tarihi: **24 Temmuz 2026**

Bu bakım sürümü PostgreSQL tespit kayıtlarını, model ve tespit türü
kataloglarıyla güvenli biçimde ilişkilendirir. Uygulama sürümü `v1.2.1`,
veritabanı şema sürümü `3` olur.

## Öne çıkanlar

- Dört aktif model ve 20 bilinen tespit türü için referans katalogları
- Bilinen ve katalog dışı sınıfları kaybetmeden yöneten `detection_types`
  çalışma zamanı sözlüğü
- `detected_objects.type_id` ve `(type_id, ts)` sorgu indeksi
- `type_id`, `model_id` ve `class_name` tutarlılığını zorlayan birleşik FK
- Eski yazma yolundan gelen kayıtların `type_id` değerini otomatik dolduran
  trigger
- Model/tür envanteri, günlük sayımlar, katalog dışı türler ve capture özeti
  için hazır PostgreSQL görünümleri

## Güvenli yükseltme

Migration eklemelidir: mevcut `model_id`, `class_name`, `confidence`, `bbox`
ve `area_ratio` alanları silinmez veya dönüştürülmez. İşlem tek transaction
ve advisory lock altında çalışır; doğrulama hatasında tamamen geri alınır.

Docker dışındaki elle kurulum için:

```bash
psql "$ROADVISION_DB_DSN" -v ON_ERROR_STOP=1 -f db/schema.sql
psql "$ROADVISION_DB_DSN" -v ON_ERROR_STOP=1 \
  -f db/roadvision_schema_v1_2_1.sql
```

Acil v1.2.0 uygulama geri dönüşü için
`db/roadvision_schema_v1_2_1_compat_rollback.sql` kullanılabilir. Bu işlem
veriyi veya yeni şema nesnelerini silmez; yalnız uygulama sürüm kapısını
yeniden açar.

## Doğrulama

- 138 otomatik test
- PostgreSQL 17 üzerinde temiz kurulum ve v2→v3 migration
- Migration'ın tekrar çalıştırılması ve uyumluluk geri dönüşü
- Bilinen ve katalog dışı türler için gerçek uygulama yazma yolu
- Canlı Docker veritabanında migration öncesi/sonrası aynı veri parmak izi

Ayrıntılı kurulum, sorgular ve geri dönüş adımları için
[DATABASE.md](DATABASE.md) dosyasına bakın.
