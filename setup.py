from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="desktop-scoreboard",
    version="0.1.0",
    author="Your Name",
    description="LED Matrix scoreboard driver for Raspberry Pi 5 with MQTT support",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=[
        "rpi-rgb-led-matrix>=1.30.0",
        "Pillow>=10.0.0",
        "paho-mqtt>=1.6.0",
        "PyYAML>=6.0",
        "python-daemon>=2.3.0",
    ],
)
