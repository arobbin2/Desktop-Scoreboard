#!/usr/bin/env python3
"""
LED matrix diagnostic test - tries different configurations
"""

import time
from PIL import Image, ImageDraw, ImageFont

try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
except ImportError:
    print("ERROR: rpi-rgb-led-matrix not installed")
    exit(1)

def test_configuration(width, height, chain_length, parallel, hardware_mapping, gpio_slowdown):
    print(f"\n{'='*60}")
    print(f"Testing: {width}x{height}, chain={chain_length}, parallel={parallel}")
    print(f"Hardware: {hardware_mapping}, GPIO slowdown: {gpio_slowdown}")
    print('='*60)
    
    try:
        options = RGBMatrixOptions()
        options.rows = height
        options.cols = width
        options.chain_length = chain_length
        options.parallel = parallel
        options.brightness = 100
        options.gpio_slowdown = gpio_slowdown
        options.hardware_mapping = hardware_mapping
        options.daemon = True
        
        matrix = RGBMatrix(options=options)
        
        # Create test image with color pattern
        total_width = width * chain_length
        total_height = height * parallel
        image = Image.new("RGB", (total_width, total_height), color=(0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw colored rectangles for each module
        module_width = width
        for i in range(chain_length):
            x_start = i * module_width
            # Alternate colors: red, green, blue, white
            colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
            color = colors[i % len(colors)]
            draw.rectangle([x_start, 0, x_start + module_width - 1, height - 1], fill=color)
        
        # Add text
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
        except:
            font = ImageFont.load_default()
        
        draw.text((10, 10), "TEST", fill=(255, 255, 255), font=font)
        
        # Display
        matrix.Clear()
        matrix.SetImage(image)
        
        print("✓ Configuration worked! Display should show colored bars + TEST text")
        time.sleep(2)
        matrix.Clear()
        return True
        
    except Exception as e:
        print(f"✗ Failed: {e}")
        return False

# Test different configurations
configs = [
    # (width, height, chain_length, parallel, hardware_mapping, gpio_slowdown)
    (64, 32, 4, 1, 'seengreat_adapter', 4),
    (64, 32, 4, 1, 'regular', 4),
    (64, 32, 4, 1, 'adafruit-hat', 4),
    (64, 32, 4, 1, 'regular', 2),
    (64, 32, 1, 1, 'seengreat_adapter', 4),  # Test single panel first
]

print("Running LED matrix diagnostic tests...")
print("Each test will display a colored pattern for 2 seconds")

for config in configs:
    if test_configuration(*config):
        print(f"\n✓✓✓ SUCCESS with config: chain={config[2]}, hardware={config[4]}, gpio_slowdown={config[5]}")
        print("Update your config.yaml with these settings!")
        break
else:
    print("\n✗ No configurations worked")
    print("Check:")
    print("  - GPIO permissions (run with sudo)")
    print("  - LED matrix power supply")
    print("  - GPIO pin connections")
    print("  - Seeed hat GPIO mapping documentation")
