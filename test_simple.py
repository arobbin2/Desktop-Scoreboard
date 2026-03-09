#!/usr/bin/env python3
"""
LED matrix simple test - minimal display without text
"""

import time
import os
from PIL import Image, ImageDraw

try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
except ImportError:
    print("ERROR: rpi-rgb-led-matrix not installed")
    exit(1)

print("LED Matrix Simple Test")
print(f"Running as UID: {os.getuid()}")
print()

# Test 1: Just try to display solid colors
configurations = [
    ("Regular mapping (default)", 'regular', 1),
    ("Seengreat adapter", 'seengreat_adapter', 1),
]

for desc, hardware_mapping, gpio_slowdown in configurations:
    print(f"\n{'='*70}")
    print(f"Testing: {desc}")
    print('='*70)
    
    try:
        print("1. Creating options...")
        options = RGBMatrixOptions()
        options.rows = 32
        options.cols = 64
        options.chain_length = 4
        options.parallel = 1
        options.brightness = 100
        options.gpio_slowdown = gpio_slowdown
        options.hardware_mapping = hardware_mapping
        options.daemon = True
        
        print("2. Initializing matrix...")
        matrix = RGBMatrix(options=options)
        print("   ✓ Matrix initialized")
        
        print("3. Creating black image...")
        image = Image.new("RGB", (256, 32), color=(0, 0, 0))
        
        print("4. Calling Clear()...")
        matrix.Clear()
        print("   ✓ Clear successful")
        
        print("5. Calling SetImage()...")
        matrix.SetImage(image)
        print("   ✓ SetImage successful (black screen)")
        
        time.sleep(1)
        
        print("6. Creating red image...")
        image = Image.new("RGB", (256, 32), color=(255, 0, 0))
        
        print("7. Setting red image...")
        matrix.SetImage(image)
        print("   ✓ SetImage successful (should be RED)")
        
        time.sleep(1)
        
        print("8. Creating green image...")
        image = Image.new("RGB", (256, 32), color=(0, 255, 0))
        
        print("9. Setting green image...")
        matrix.SetImage(image)
        print("   ✓ SetImage successful (should be GREEN)")
        
        time.sleep(1)
        
        print("10. Clearing...")
        matrix.Clear()
        
        print("\n✓✓✓ SUCCESS!")
        print(f"Configuration '{desc}' works!")
        print("Update config.yaml with:")
        print(f"  hardware_mapping: {hardware_mapping}")
        print(f"  gpio_slowdown: {gpio_slowdown}")
        break
        
    except Exception as e:
        print(f"✗ Failed at step: {e}")
        import traceback
        traceback.print_exc()

print("\nDone!")
