# STRIX-1 — "How it works" presentation script

For the slides in [`presentation/STRIX-1_How_It_Works.pdf`](presentation/STRIX-1_How_It_Works.pdf). It starts
right after your introduction and runs about **5½ minutes** at a relaxed pace (≈ 130 words per minute).
For a 4-minute slot, say only the first two sentences on slides 2, 3, 5 and 9.
Slide numbers match the bottom-right corner of each slide. Lines in *italics* are stage directions.

## 1 · Sense. Understand. Remember. Advise. (≈ 20 s)

So how does STRIX-1 actually work? It runs one loop, over and over: it senses, it understands, it
remembers, and it advises the pilot. And every part of that loop runs on the Raspberry Pi 5 that sits on
the drone. Nothing goes to the cloud.

## 2 · One loop, all on the drone (≈ 40 s)

On the left are the senses: the Camera Module 3 Wide, the MPU6050 motion sensor, and the DHT11 for
temperature and humidity. In the middle, the Pi does the thinking. YOLO finds objects, sensor fusion
works out how the drone is tilted, the object memory keeps track of things, and a local Qwen model plans
and answers. On the right are the results: the camera servo moves, the pilot gets advice, and everything
shows up live in any browser. The pilot always flies the drone. STRIX-1 only helps.

## 3 · What's on the drone (≈ 30 s)

Here's the wiring. The SG90 servo that tilts the camera takes its signal from GPIO 18, pin 12, and gets
its power from the 52Pi board's 5 volts. The MPU6050 talks over I2C on pins 3 and 5. The DHT11 sends its
readings on GPIO 4, pin 7. The camera plugs into the Pi with a ribbon cable, and the whole thing runs on
a USB-C power bank.

## 4 · It sees (≈ 30 s)

First, it sees. YOLO looks at the camera feed about three times a second and finds objects like people,
phones and cups. Then STRIX-1 works out how far away each one is. It knows roughly how big a cup is, and
it knows our lens, so the size in the picture gives the distance. That's an estimate, so we always label
it that way: "cup, about 0.7 metres".

## 5 · It feels the drone (≈ 30 s)

Second, it feels the drone. The MPU6050 is read a hundred times a second and turned into roll, pitch and
yaw. *(point at the 3D model)* This 3D drone moves exactly like the real one. That same data keeps the
memory in the right place when the drone turns. The DHT11 adds temperature and humidity, so when you ask
"how warm is it?", Qwen answers from the sensor, not from a guess.

## 6 · It aims the camera (≈ 35 s)

Third, it aims. You can ask in plain English: "find the person and keep them centered." Qwen turns that
into a small plan: find a person, then track them. Because the plan has to follow a strict format, even a
small model can't send a broken command. Then the SG90 servo tilts the camera until the person is in the
middle. It's safe by design: it stays inside its limits, moves smoothly, and one key, the space bar,
stops it instantly.

## 7 · It remembers (≈ 40 s)

Fourth, and this is my favourite part, it remembers. Every object is either seen, remembered or hidden.
*(point at the three pictures)* Here a cup covers the ball, and STRIX-1 marks the ball as hidden. Then
the cups get shuffled, and the memory moves the ball along with its cup. In the 3D view you can see the
ball glowing inside it. Ask "where is the ball?" and the camera aims back at the right cup. When the cup
is lifted, it checks itself: "memory was right."

## 8 · Built for the edge (≈ 25 s)

All of this runs on a Raspberry Pi. There are no cloud calls, the Qwen model has two billion parameters,
and we have over 160 automated tests. A Pi running AI gets hot, so STRIX-1 watches its own temperature.
Around 75 degrees it slows the AI down, well before the Pi would throttle itself.

## 9 · Impact on today's world (≈ 40 s)

So why does this matter? Drones are already used to search for people and inspect roofs, bridges and
crops. A co-pilot that remembers what it saw, even after it leaves the picture, makes that job easier.
Because there's no cloud, the video stays private, and it keeps working where there's no signal, which is
often exactly where help is needed. It's built from parts anyone can buy, with open-source code. And
it's honest: it labels its estimates, never fakes GPS or battery, and the pilot stays in control.

## 10 · What's next (≈ 20 s)

Next, we want to fly it and test every sensor in the air. We'd like to add a second servo so the camera
can pan, a depth sensor for better distances, and an AI accelerator for faster vision.

## 11 · Thank you (≈ 10 s)

That's STRIX-1. It sees, aims, feels and remembers, all on the drone. The code is on GitHub. Scan the QR
code to try it. Thank you!

---

## Editing the slides in Canva

1. In Canva, choose **Create a design → Import file** (or drag the PDF onto the Canva home page).
2. Each page becomes a slide. Headings, paragraphs and labels are editable text; screenshots and the wiring
   diagram are images; boxes and arrows are shapes.
3. The slides use **Barlow** and **JetBrains Mono**, which are in Canva's font library, so the text keeps
   its look. Canva may split long paragraphs into one text box per line; select them together to edit.
4. Swap in your own photos of the real drone on slides 3, 5 and 6 for the strongest presentation.
