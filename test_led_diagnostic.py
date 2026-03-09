#!/usr/bin/env python3
"""
LED matrix diagnostic test - with detailed error reporting
"""

import time
import os
from PIL import Image, ImageDraw, ImageFont

try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
except ImportError:
    print("ERROR: rpi-rgb-led-matrix not installed")
    exit(1)

print("Starting LED matrix diagnostic...")
print(f"Running as user: {os.getuid()}")
print(f"Library path: {__import__('rgbmatrix').__file__}")

def test_configuration(width, height, chain_length, parallel, hardware_mapping, gpio_slowdown, test_num):
    print(f"\n{'='*70}")
    print(f"TEST {test_num}: {width}x{height}, chain={chain_length}, parallel={parallel}")
    print(f"        Hardware: {hardware_mapping}, GPIO slowdown: {gpio_slowdown}")
    print('='*70)
    
    try:
        print("Creating RGBMatrixOptions...")
        options = RGBMatrixOptions()
        options.rows = height
        options.cols = width
        options.chain_length = chain_length
        options.parallel = parallel
        options.brightness = 100
        options.gpio_slowdown = gpio_slowdown
        options.hardware_mapping = hardware_mapping
        options.daemon = True
        
        print("Initializing matrix...")
        matrix = RGBMatrix(options=options)
        print("✓ Matrix initialized successfully")
        
        # Create test image
        print("Creating test image...")
        total_width = width * chain_length
        total_height = height * parallel
        image = Image.new("RGB", (total_width, total_height), color=(0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw colored rectangles for each module
        module_width = width
        for i in range(chain_length):
            x_start = i * module_width
            colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
            color = colors[i % len(colors)]
            draw.rectangle([x_start, 0, x_start + module_width - 1, total_height - 1], fill=color)
        
        # Add text
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
        except:
            font = ImageFont.load_default()
        
        draw.text((10, 10), "TEST", fill=(255, 255, 255), font=font)
        
        print("Displaying image (2 seconds)...")
        matrix.Clear()
        matrix.SetImage(image)
        
        print("✓✓✓ Configuration WORKED!")
        print("Display should show colored bars: RED, GREEN, BLUE, WHITE")
        time.sleep(2)
        matrix.Clear()
        return True
        
    except Exception as e:
        print(f"✗ FAILED with error:")
        print(f"  {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

# Test configurations
configs = [
    (64, 32, 4, 1, 'seengreat_adapter', 4, "Seengreat 4-panel"),
    (64, 32, 1, 1, 'seengreat_adapter', 4, "Seengreat single panel"),
    (64, 32, 4, 1, 'regular', 4, "Regular 4-panel"),
    (64, 32, 1, 1, 'regular', 4, "Regular single panel"),
    (64, 32, 4, 1, 'adafruit-hat', 4, "Adafruit HAT"),
]

print("\nRunning LED matrix diagnostic tests...")
print("NOTE: Run with 'sudo python3' for GPIO access\n")

success_configs = []
for idx, (w, h, ch, p, hm, gs, desc) in enumerate(configs, 1):
    try:
        if test_configuration(w, h, ch, p, hm, gs, idx):
            success_configs.append(f"✓ {desc} (chain={ch}, mapping={hm}, slowdown={gs})")
    except Exception as e:
        print(f"✗ TEST {idx} crashed: {e}")
        import traceback
        traceback.print_exc()

print(f"\n{'='*70}")
print("RESULTS:")
print('='*70)
if success_configs:
    print("\n✓ Working configurations:")
    for config in success_configs:
        print(f"  {config}")
    print("\nUpdate your config.yaml with a working configuration!")
else:
    print("\n✗ No configurations worked")
    print("\nTroubleshooting checklist:")
    print("  1. Are you running with 'sudo'? (GPIO requires root)")
    print("  2. Check LED matrix power supply and connections")
    print("  3. Check GPIO pin connections to the hat")
    print("  4. Check `/tmp/rpi-rgb-led-matrix/adapter/` for custom mappings")
    print("  5. Try different gpio_slowdown values (1, 2, 3, 4)")
    print("  6. Check for /dev/mem access: ls -la /dev/mem")

print()
