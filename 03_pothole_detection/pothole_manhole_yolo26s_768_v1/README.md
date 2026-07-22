# Pothole + Manhole Cover YOLO26s

Final selected two-class road object detection model.

Classes:
- 0: pothole
- 1: manhole_cover

Input size: 768
Recommended starting confidence: 0.25
Best training epoch: 196

Final standard test:
- Overall mAP50: 0.878024
- Overall mAP50-95: 0.661832
- Pothole mAP50: 0.805651
- Manhole cover mAP50: 0.950398

Operational confusion test at conf=0.25:
- 55/58 manholes classified correctly
- 1/58 manholes classified as pothole
- 2/58 manholes missed

Use the .pt model in this directory for validation, export and deployment.
