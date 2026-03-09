#!/usr/bin/env python3
"""
Simple LED matrix test script - displays test text on the matrix
Run with: sudo python3 test_led.py
"""

import time
from PIL import Image, ImageDraw, ImageFont

try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
except ImportError:
    print("ERROR: rpi-rgb-led-matrix not installed")
    print("Install with: make && cd python && python3 setup.py install --user")
    exit(1)

# Matrix configuration (4x 64x32 modules chained)
options = RGBMatrixOptions()
options.rows = 32
options.cols = 64
options.chain_length = 4  # 4 modules horizontally
options.parallel = 1
options.brightness = 100
options.gpio_slowdown = 4
options.hardware_mapping = 'seengreat_adapter'  # Custom Seengreat GPIO mapping
options.daemon = True

try:
    matrix = RGBMatrix(options=options)
except Exception as e:
    print(f"ERROR initializing matrix: {e}")
    print("Make sure you're running with sudo: sudo python3 test_led.py")
    exit(1)

# Create image and draw test text
width = 64 * 4  # Total width (256)
height = 32
image = Image.new("RGB", (width, height), color=(0, 0, 0))
draw = ImageDraw.Draw(image)

try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
except OSError:
    font = ImageFont.load_default()

# Draw test text
text = "TEST TEXT"
bbox = draw.textbbox((0, 0), text, font=font)
text_width = bbox[2] - bbox[0]
x = (width - text_width) // 2
y = (height - 20) // 2

draw.text((x, y), text, fill=(255, 0, 0), font=font)  # Red text

# Display on matrix
matrix.SetImage(image)
matrix.Clear()
matrix.SetImage(image)

print("TEST TEXT displayed on LED matrix")
print("Press Ctrl+C to exit")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nClearing matrix and exiting...")
    matrix.Clear()
