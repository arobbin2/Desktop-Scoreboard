# Desktop-Scoreboard

LED Matrix scoreboard driver for Raspberry Pi 5 with MQTT support. Receives scoreboard data from Node-RED via MQTT and displays it on RGB LED matrix panels.

## Features

- **MQTT Integration**: Receive real-time scoreboard updates from Node-RED
- **LED Matrix Support**: Drive RGB LED matrices using rpi-rgb-led-matrix
- **Flexible Display**: Show both text and structured scoreboard data (teams, scores, status)
- **Display Modes**: Switch between `scoreboard`, `clock`, `rss` ticker, `cubs` live game mode, and an opt-in `crown` meter mode scaffold
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
# Build and install Python bindings from source
python3 -m pip install --break-system-packages .
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
  brightness: 40
  gpio_slowdown: 10
  hardware_mapping: regular        # Known-good default for direct GPIO wiring
  chain_length: 4                  # Number of chained panels horizontally
  parallel: 1                      # Number of parallel chains

modes:
  default_mode: scoreboard         # scoreboard | clock | rss | cubs | crown

rss:
  feed_url: https://news.google.com/rss
  refresh_seconds: 300
  scroll_step: 1

cubs:
  team_id: 112                     # Chicago Cubs
  refresh_seconds: 15              # Live game refresh cadence
  off_day_text: "No Cubs Game Today"

crown:
  enabled: false                   # Keep off until feed is validated
  meter_topic: scoreboard/crown/meter
  udp_enabled: true                # Read Crown meter from UDP packet stream
  udp_bind_host: 0.0.0.0
  udp_bind_port: 10001
  udp_hex_byte_index: 0            # Select byte from parsed hex payload
  meter_min_db: -60.0
  meter_max_db: 0.0
  stale_after_seconds: 5.0
  fallback_to_scoreboard_on_stale: true
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
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/home/techserv/Desktop-Scoreboard
Environment=HOME=/home/techserv
Environment=PYTHONPATH=/home/techserv/Desktop-Scoreboard:/home/techserv/.local/lib/python3.13/site-packages
ExecStart=/usr/bin/python3 -m src.app
Restart=always
RestartSec=2
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable scoreboard
sudo systemctl restart scoreboard
sudo systemctl status scoreboard --no-pager -l
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

### Mode Control Message

Publish to `scoreboard/control`.

String payload examples:

```
mode:rss
```

```
clock
```

JSON payload examples:

```json
{
  "mode": "rss"
}
```

```json
{
  "mode": "rss",
  "feed_url": "https://feeds.bbci.co.uk/news/world/rss.xml",
  "refresh_seconds": 180,
  "rss_refresh_now": true
}
```

```json
{
  "mode": "cubs",
  "cubs_refresh_seconds": 10,
  "cubs_refresh_now": true
}
```

Supported modes:

- `scoreboard`: MQTT text/data messages render normally; idle clock can still take over
- `clock`: Always shows the live clock/weather display
- `rss`: Shows scrolling headlines from configured RSS/Atom feed
- `cubs`: Shows current Cubs game score, inning, ball/strike/out count, and base occupancy
- `crown`: Crown amplifier metering scaffold; requires `crown.enabled: true` before mode switches are accepted

Crown UDP meter payload notes:

- The app accepts either raw UDP bytes or ASCII hex payloads.
- ASCII examples accepted: `7F`, `0x7F`, `7F 1A`, `7F,1A`.
- `udp_hex_byte_index` chooses which parsed byte is used for the single meter.

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
- If the library reports `snd_bcm2835` conflict, disable Pi onboard audio and reboot

### Service Starts Then Immediately Stops

- If logs show `Starting scoreboard application` then `Deactivated successfully`, confirm you have the latest code where matrix daemon mode is disabled for systemd
- Run foreground once to compare behavior:
  - `sudo -E env PYTHONPATH="$HOME/.local/lib/python3.13/site-packages" python3 -m src.app`
- Verify service environment includes project + user site-packages in `PYTHONPATH`

### Display Not Showing

- Verify LED matrix power supply and ribbon orientation
- Confirm `hardware_mapping` is lowercase `regular` unless you installed a custom mapping build
- Run a direct pixel test to isolate app vs hardware:
  - `sudo -E env PYTHONPATH="$HOME/.local/lib/python3.13/site-packages" python3 -c "from rgbmatrix import RGBMatrix,RGBMatrixOptions; import time; o=RGBMatrixOptions(); o.rows=32; o.cols=64; o.chain_length=1; o.parallel=1; o.gpio_slowdown=10; o.hardware_mapping='regular'; m=RGBMatrix(options=o); c=m.CreateFrameCanvas(); c.Fill(255,0,0); m.SwapOnVSync(c); time.sleep(4)"`
- Review logs: `journalctl -u scoreboard -f` (if using systemd)

## License

MIT

## Author

Your Name (@arobbin2)
