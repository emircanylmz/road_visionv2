# Roadline semantic segmentation

Secilen agirlik kaynagi:

`/content/drive/MyDrive/roadline_training/yolo26s_sem_1024_v1/weights/best.pt`

Calisma klasoru:

`/content/drive/MyDrive/roadline_training/yolo26s_sem_1024_v1`

Hedef agirlik adi: `model.pt`.

Test foreground mIoU: `0.6643939018249512`. Test pixel accuracy: `0.9939372539520264`.

`test_results/` icinde iki confusion matrix, IoU grafigi ve etiket ornegi bulunur. Egitim ayarlari `args.yaml` dosyasindadir.
