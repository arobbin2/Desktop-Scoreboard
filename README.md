# Desktop-Scoreboard

LED Matrix scoreboard driver for Raspberry Pi 5 with MQTT support. Receives scoreboard data from Node-RED via MQTT and displays it on RGB LED matrix panels.

## Features

- **MQTT Integration**: Receive real-time scoreboard updates from Node-RED
- **LED Matrix Support**: Drive RGB LED matrices using rpi-rgb-led-matrix
- **Flexible Display**: Show both text and structured scoreboard data (teams, scores, status)
- **Configurable**: YAML-based configuration for MQTT, matrix hardware, and display settings
- **Mock Mode**: Test without hardware using mock display functions
- **Graceful Shutdown**: Proper signal handling for clean application termination

## Prerequisites

### Raspberry Pi 5 Setup

1. **Python 3.8+** installed
2. **GPIO libraries** for LED matrix control
3. **MQTT Broker** (can be local or remote)
4. **Node-RED** (for data processing and MQTT publishing)

### System Dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-dev \
    python3-pip \
    build-essential \
    libopenjp2-7 \
    libtiff6 \
    libjasper1 \
    libharfbuzz0b \
    libwebp6
```

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/arobbin2/Desktop-Scoreboard.git
cd Desktop-Scoreboard
```

### 2. Create Virtual Environment (Recommended)

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Install LED Matrix Libraries (Raspberry Pi Only)

The `rpi-rgb-led-matrix` library requires special build-time setup:

```bash
cd /tmp
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd rpi-rgb-led-matrix
make install-python PYTHON=$(which python3)
cd ~/Desktop-Scoreboard
```

Or, if using pip installation:
```bash
pip install rpi-rgb-led-matrix
```

## Configuration

Edit `config/config.yaml` to match your setup:

```yaml
mqtt:
  broker_host: 192.168.1.100      # Your MQTT broker IP
  broker_port: 1883
  subscriptions:
    - scoreboard/data              # Topic for structured data
    - scoreboard/text              # Topic for text messages

matrix:
  width: 64                        # Panel width in pixels
  height: 32                       # Panel height in pixels
  brightness: 100
  hardware_mapping: adafruit-hat   # See rpi-rgb-led-matrix docs for options
  chain_length: 1                  # Number of chained panels horizontally
  parallel: 1                      # Number of parallel chains
```

## Building/Running

### Development (Mock Mode - No Hardware Required)

```bash
# Run with mock LED display
python3 -m src.app
```

### Production (On Raspberry Pi with Hardware)

```bash
# Run the application (requires root for GPIO access)
sudo python3 -m src.app
```

Or as a systemd service (recommended):

```bash
# Create service file
sudo nano /etc/systemd/system/scoreboard.service
```

```ini
[Unit]
Description=Desktop Scoreboard
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Desktop-Scoreboard
ExecStart=/home/pi/Desktop-Scoreboard/venv/bin/python3 -m src.app
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable scoreboard
sudo systemctl start scoreboard
sudo systemctl status scoreboard
```

## MQTT Message Format

### Structured Scoreboard Data

Publish JSON to `scoreboard/data`:

```json
{
  "team1": "HOME",
  "team2": "AWAY",
  "score1": 24,
  "score2": 21,
  "status": "2nd Quarter",
  "time": "5:30"
}
```

### Simple Text Message

Publish text to `scoreboard/text`:

```
SCORE: 24-21
```

## Node-RED Integration

Example Node-RED flow to send data to the scoreboard:

1. **Input Node**: MQTT Subscribe to your data source
2. **Processing Node**: Format/parse your data
3. **Output Node**: MQTT Publish to `scoreboard/data`

Example payload transformation:
```javascript
return {
  payload: {
    team1: msg.payload.home_team,
    team2: msg.payload.away_team,
    score1: msg.payload.home_score,
    score2: msg.payload.away_score,
    status: msg.payload.quarter
  }
};
```

## Testing

Run the test suite:

```bash
python3 -m pytest tests/
```

Or using unittest:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

## Project Structure

```
Desktop-Scoreboard/
├── src/
│   ├── __init__.py
│   ├── app.py              # Main application
│   ├── scoreboard.py       # LED matrix controller
│   └── mqtt_client.py      # MQTT client
├── config/
│   └── config.yaml         # Configuration file
├── tests/
│   ├── __init__.py
│   └── test_scoreboard.py  # Unit tests
├── requirements.txt        # Python dependencies
├── setup.py               # Package setup
└── README.md
```

## Troubleshooting

### MQTT Connection Issues

- Verify MQTT broker is running: `mosquitto -v`
- Check broker IP/port in `config/config.yaml`
- Test connectivity: `mosquitto_pub -h localhost -t test -m "hello"`

### GPIO/Hardware Issues

- Ensure running with `sudo` on Raspberry Pi
- Check GPIO permissions: `groups $(whoami) | grep gpio`
- Try mock mode first to verify the application logic

### Display Not Showing

- Verify LED matrix power supply
- Check GPIO pin configuration matches your hardware
- Review logs: `journalctl -u scoreboard -f` (if using systemd)

## License

MIT

## Author

Your Name (@arobbin2)
