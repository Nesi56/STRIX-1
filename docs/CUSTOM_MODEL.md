# Teach the fleet your own objects (custom YOLO model)

STRIX-1's YOLO detector loads any YOLO11 / YOLOv8 / YOLOX model exported to ONNX, with your own class names.
Put the **same model on every drone**, and the hub's **Fleet contacts** panel merges what all of them see.
The examples below use search-and-rescue classes (life jacket, kayak, tent); use whatever you need.

STRIX-1 stays advisory with a custom model too: it reports what it sees and where, and the operator decides.

## 1. Collect pictures

Aim for a few hundred images per class, from the angles and heights the drones will fly at.

* **From the drones:** the camera button under the video saves a snapshot to `data/media/snapshots/`, and
  recordings save video you can pull frames from (`ffmpeg -i video.mp4 -vf fps=2 frames/%05d.jpg`).
* **From the simulator:** add similar objects to `sim/worlds/strix_field.objects` and save snapshots from the
  simulated cameras ([SIMULATION.md](SIMULATION.md)). Mix them with real photos: simulated images alone
  rarely train a model that works outdoors.

## 2. Label them

Draw a box around every object in every image with a free labelling tool. Both of these can run on your own
computer, with no data leaving it:

* [Label Studio](https://labelstud.io) (`pip install label-studio`, then `label-studio`)
* [CVAT](https://www.cvat.ai) (self-hosted with Docker)

Export in **YOLO** format. You get `images/`, `labels/` and a `data.yaml` that lists your class names:

```yaml
path: sar-dataset
train: images/train
val: images/val
names: [life jacket, kayak, tent]
```

## 3. Train

On a computer with an NVIDIA GPU (or a free Google Colab GPU), with [Ultralytics](https://docs.ultralytics.com):

```bash
pip install ultralytics
```

```bash
yolo detect train data=data.yaml model=yolo11n.pt imgsz=640 epochs=100
```

`yolo11n` is the small, fast version that suits the Pi. Training starts from a pretrained model, so a few
hundred images per class goes a long way.

## 4. Export for the Pi

```bash
yolo export model=runs/detect/train/weights/best.pt format=onnx imgsz=416
```

`imgsz=416` runs faster and cooler on the Pi than 640, at some cost in range.

## 5. Install it on every drone

Copy the model into `models/` and put the class names next to it, **in the same order as in `data.yaml`**, one
per line:

```bash
scp runs/detect/train/weights/best.onnx pi@strix-alpha.local:~/STRIX-1/models/sar.onnx
```

```bash
printf 'life jacket\nkayak\ntent\n' > sar.labels.txt && scp sar.labels.txt pi@strix-alpha.local:~/STRIX-1/models/
```

Then, in the drone's `.env`:

```
STRIX_DETECTOR_MODEL=models/sar.onnx
STRIX_DETECTOR_PROMPTS=life jacket,kayak,tent
STRIX_DETECTOR_SIZE_PRIORS=life jacket=0.6,kayak=0.45,tent=1.2
```

* `PROMPTS` picks which classes are shown on the video (you can change them later under "YOLO looks for").
* `SIZE_PRIORS` gives each class's typical height in metres, which STRIX-1 needs to estimate distance. A class
  without one shows no distance; it is never guessed.

Restart STRIX-1 (`sudo systemctl restart strix1`) and repeat for every drone. The status corner then shows
YOLO running your model.

## Checking the result

* **One drone:** point the camera at the object. The video shows boxes with your class names.
* **The fleet:** the hub's **Fleet contacts** panel lists each class the fleet sees, how many, which drones see
  it, and the nearest distance.
* **Before trusting it:** count how often it misses an object or reports one that isn't there, in conditions
  like the real ones (light, height, background). A model that works on your training photos can still fail
  outdoors.
