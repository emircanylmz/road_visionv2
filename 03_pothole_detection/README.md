# Pothole ve rögar kapağı detection

## Aktif model

Uygulamanın `models.json` kataloğunda seçili ağırlık:

`pothole_manhole_yolo26s_768_v1/pothole_manhole_yolo26s_768_v1.pt`

Drive kaynağı:

`/content/drive/MyDrive/pothole_manhole_training/selected_pothole_manhole_yolo26s_768_v1/pothole_manhole_yolo26s_768_v1.pt`

Sınıflar:

- `0`: `pothole`
- `1`: `manhole_cover`

Best epoch `196`, early stopping epoch `231`.

Final test: precision `0.899455`, recall `0.846949`, mAP50 `0.878024`, mAP50-95 `0.661832`.

Aktif modelin ayrıntılı metrikleri, confusion matrix dosyaları, bilinen hata denetimi ve checksum kayıtları kendi alt klasöründedir.

## Korunan eski model

Eski tek sınıflı çukur modeli `model.pt`, ona ait `metrics.json` ve `test_results/` dosyaları karşılaştırma ve geri dönüş amacıyla değiştirilmeden korunur. Uygulama varsayılan olarak bu eski ağırlığı yüklemez.
