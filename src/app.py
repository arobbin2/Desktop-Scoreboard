"""Main application - ties together MQTT and LED display"""

import logging
import signal
import sys
import time
import yaml
from typing import Dict, Any, Optional, Tuple

from src.mqtt_client import ScoreboardMQTTClient
from src.scoreboard import LEDScoreboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ScoreboardApp:
    """Main application controller"""

    def __init__(self, config_path: str = "config/config.yaml"):
        """
        Initialize the scoreboard application

        Args:
            config_path: Path to configuration file
        """
        self.config = self._load_config(config_path)
        self.mqtt_client: Optional[ScoreboardMQTTClient] = None
        self.scoreboard: Optional[LEDScoreboard] = None
        self.running = False
        self.last_message_time = 0.0
        self.last_clock_second: Optional[int] = None

        clock_config = self.config.get("clock") or {}
        self.clock_enabled = bool(clock_config.get("enabled", False))
        self.clock_format = str(clock_config.get("format", "%H:%M:%S"))
        self.clock_idle_after_seconds = float(clock_config.get("idle_after_seconds", 0))
        self.clock_font_size: Optional[int] = None

        raw_font_size = clock_config.get("font_size")
        if raw_font_size is not None:
            try:
                parsed_font_size = int(raw_font_size)
                if parsed_font_size > 0:
                    self.clock_font_size = parsed_font_size
                else:
                    logger.warning("clock.font_size must be > 0. Using automatic sizing.")
            except (TypeError, ValueError):
                logger.warning("Invalid clock.font_size value. Using automatic sizing.")

        color = clock_config.get("color", [255, 255, 0])
        if isinstance(color, list) and len(color) == 3:
            self.clock_color: Tuple[int, int, int] = (int(color[0]), int(color[1]), int(color[2]))
        else:
            self.clock_color = (255, 255, 0)

        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            logger.info(f"Configuration loaded from {config_path}")
            if not isinstance(config, dict):
                logger.warning("Config root is not a mapping. Using defaults.")
                return self._get_default_config()
            return config
        except FileNotFoundError:
            logger.warning(f"Config file not found: {config_path}. Using defaults.")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration"""
        return {
            "mqtt": {
                "broker_host": "localhost",
                "broker_port": 1883,
                "client_id": "scoreboard",
                "subscriptions": ["scoreboard/data"],
            },
            "matrix": {
                "width": 64,
                "height": 32,
                "brightness": 100,
                "gpio_slowdown": 4,
                "hardware_mapping": "adafruit-hat",
                "title_text": "COMPTON",
            },
        }

    def start(self) -> None:
        """Start the scoreboard application"""
        logger.info("Starting scoreboard application")
        self.running = True

        try:
            # Initialize LED matrix
            matrix_config = self.config.get("matrix") or {}
            self.scoreboard = LEDScoreboard(**matrix_config)
            self.scoreboard.display_text("Ready", color=(0, 255, 0))

            # Initialize MQTT client
            mqtt_config = self.config.get("mqtt") or {}
            subscriptions = mqtt_config.pop("subscriptions", ["scoreboard/data"])
            self.mqtt_client = ScoreboardMQTTClient(**mqtt_config)
            self.mqtt_client.set_message_callback(self._on_mqtt_message)
            self.mqtt_client.connect()
            self.mqtt_client.start()

            # Subscribe to topics
            for topic in subscriptions:
                self.mqtt_client.subscribe(topic)

            logger.info("Scoreboard application running")

            # Keep the application running
            while self.running:
                self._maybe_render_clock()
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"Error starting application: {e}", exc_info=True)
            self.stop()

    def _on_mqtt_message(self, topic: str, payload: Any) -> None:
        """Handle incoming MQTT message"""
        logger.debug(f"Received message on {topic}: {payload}")
        self.last_message_time = time.time()

        try:
            if self.scoreboard is None:
                return

            # Handle different message types
            if isinstance(payload, dict):
                # Structured data with scores, teams, etc.
                self.scoreboard.display_data(payload)
            elif isinstance(payload, str):
                # Simple text message
                self.scoreboard.display_text(payload)
            else:
                # Convert to string
                self.scoreboard.display_text(str(payload))

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}", exc_info=True)

    def _maybe_render_clock(self) -> None:
        """Render a live clock when enabled and not actively showing MQTT updates."""
        if not self.clock_enabled or self.scoreboard is None:
            return

        now = time.time()

        # If configured, only show clock after a quiet period since last MQTT update.
        if self.clock_idle_after_seconds > 0 and self.last_message_time > 0:
            if (now - self.last_message_time) < self.clock_idle_after_seconds:
                return

        current_second = int(now)
        if self.last_clock_second == current_second:
            return

        self.last_clock_second = current_second
        clock_text = time.strftime(self.clock_format, time.localtime(now))
        self.scoreboard.display_clock(
            clock_text,
            color=self.clock_color,
            font_size=self.clock_font_size,
        )

    def _signal_handler(self, signum, frame):
        """Handle termination signals"""
        logger.info(f"Received signal {signum}. Shutting down gracefully...")
        self.stop()

    def stop(self) -> None:
        """Stop the scoreboard application"""
        if not self.running:
            return

        logger.info("Stopping scoreboard application")
        self.running = False

        if self.mqtt_client:
            self.mqtt_client.stop()

        if self.scoreboard:
            self.scoreboard.shutdown()

        logger.info("Scoreboard application stopped")


def main():
    """Main entry point"""
    app = ScoreboardApp()
    app.start()


if __name__ == "__main__":
    main()
