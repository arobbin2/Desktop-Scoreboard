"""Main application - ties together MQTT and LED display"""

import logging
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import json
import xml.etree.ElementTree as ET
import yaml
from typing import Dict, Any, Optional, Tuple, List

from src.mqtt_client import ScoreboardMQTTClient
from src.scoreboard import LEDScoreboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ScoreboardApp:
    """Main application controller"""

    VALID_MODES = {"scoreboard", "clock", "rss"}

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
        self.active_mode = "scoreboard"

        app_config = self.config.get("app") or {}
        self.main_loop_sleep_seconds = self._as_float(
            app_config.get("main_loop_sleep_seconds"),
            fallback=0.005,
            minimum=0.001,
        )

        clock_config = self.config.get("clock") or {}
        self.clock_enabled = bool(clock_config.get("enabled", False))
        self.clock_format = str(clock_config.get("format", "%H:%M:%S"))
        self.clock_idle_after_seconds = float(clock_config.get("idle_after_seconds", 0))
        self.clock_font_size: Optional[int] = None
        self.weather_temperature_text: Optional[str] = None
        self.weather_next_fetch_time = 0.0

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

        weather_config = clock_config.get("weather") or {}
        self.weather_enabled = bool(weather_config.get("enabled", True))

        try:
            self.weather_latitude = float(weather_config.get("latitude", 41.7056))
            self.weather_longitude = float(weather_config.get("longitude", -86.2353))
        except (TypeError, ValueError):
            logger.warning("Invalid weather coordinates. Falling back to Notre Dame, IN.")
            self.weather_latitude = 41.7056
            self.weather_longitude = -86.2353

        self.weather_unit = str(weather_config.get("unit", "F")).strip().upper()
        if self.weather_unit not in {"F", "C"}:
            logger.warning("Invalid weather.unit. Using Fahrenheit.")
            self.weather_unit = "F"

        weather_color = weather_config.get("color", [0, 102, 255])
        if isinstance(weather_color, list) and len(weather_color) == 3:
            self.weather_color: Tuple[int, int, int] = (
                int(weather_color[0]),
                int(weather_color[1]),
                int(weather_color[2]),
            )
        else:
            self.weather_color = (0, 102, 255)

        try:
            self.weather_refresh_seconds = max(60, int(weather_config.get("refresh_seconds", 600)))
        except (TypeError, ValueError):
            self.weather_refresh_seconds = 600

        try:
            self.weather_timeout_seconds = max(1.0, float(weather_config.get("timeout_seconds", 2.5)))
        except (TypeError, ValueError):
            self.weather_timeout_seconds = 2.5

        mode_config = self.config.get("modes") or {}
        requested_default_mode = str(mode_config.get("default_mode", "scoreboard"))
        self.default_mode = self._normalize_mode(requested_default_mode)
        self.active_mode = self.default_mode

        rss_config = self.config.get("rss") or {}
        self.rss_feed_url = str(rss_config.get("feed_url", "https://news.google.com/rss")).strip()
        self.rss_timeout_seconds = self._as_float(rss_config.get("timeout_seconds"), fallback=4.0, minimum=1.0)
        self.rss_refresh_seconds = self._as_int(rss_config.get("refresh_seconds"), fallback=300, minimum=30)
        self.rss_scroll_step = self._as_int(rss_config.get("scroll_step"), fallback=1, minimum=1)
        self.rss_frame_interval_seconds = self._as_float(
            rss_config.get("frame_interval_seconds"),
            fallback=0.08,
            minimum=0.01,
        )
        self.rss_max_elapsed_factor = self._as_float(
            rss_config.get("max_elapsed_factor"),
            fallback=2.0,
            minimum=1.0,
        )
        derived_pixels_per_second = max(
            1.0,
            float(self.rss_scroll_step) / float(self.rss_frame_interval_seconds),
        )
        self.rss_scroll_pixels_per_second = self._as_float(
            rss_config.get("scroll_pixels_per_second"),
            fallback=derived_pixels_per_second,
            minimum=1.0,
        )
        self.rss_ticker_gap = self._as_int(rss_config.get("ticker_gap"), fallback=24, minimum=8)
        self.rss_font_size = self._as_optional_int(rss_config.get("font_size"), minimum=8)
        self.rss_fallback_text = str(rss_config.get("fallback_text", "RSS: waiting for headlines")).strip()

        rss_color = rss_config.get("color", [255, 255, 255])
        if isinstance(rss_color, list) and len(rss_color) == 3:
            self.rss_color: Tuple[int, int, int] = (
                int(rss_color[0]),
                int(rss_color[1]),
                int(rss_color[2]),
            )
        else:
            self.rss_color = (255, 255, 255)

        self.rss_headlines: List[str] = []
        self.rss_ticker_text = self.rss_fallback_text or "RSS"
        self.rss_scroll_px = 0
        self.rss_scroll_px_float = 0.0
        self.rss_last_frame_time = 0.0
        self.rss_next_fetch_time = 0.0

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

            if self.active_mode == "rss":
                self._refresh_rss_if_due(now=time.time(), force=True)

            # Keep the application running
            while self.running:
                self._tick_active_mode()
                time.sleep(self.main_loop_sleep_seconds)

        except Exception as e:
            logger.error(f"Error starting application: {e}", exc_info=True)
            self.stop()

    def _on_mqtt_message(self, topic: str, payload: Any) -> None:
        """Handle incoming MQTT message"""
        logger.debug(f"Received message on {topic}: {payload}")

        normalized_topic = str(topic).strip().lower()
        if normalized_topic.endswith("/control"):
            self._handle_control_message(payload)
            return

        self.last_message_time = time.time()

        try:
            if self.scoreboard is None:
                return

            if self.active_mode != "scoreboard":
                logger.debug(
                    f"Ignoring display payload while mode '{self.active_mode}' is active"
                )
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

    def _tick_active_mode(self) -> None:
        """Render whichever mode is currently active."""
        now = time.time()

        if self.active_mode == "rss":
            self._tick_rss_mode(now)
            return

        if self.active_mode == "clock":
            self._maybe_render_clock(now, respect_idle=False)
            return

        self._maybe_render_clock(now, respect_idle=True)

    def _maybe_render_clock(self, now: float, respect_idle: bool) -> None:
        """Render a live clock when enabled and not actively showing MQTT updates."""
        if not self.clock_enabled or self.scoreboard is None:
            return

        # If configured, only show clock after a quiet period since last MQTT update.
        if respect_idle and self.clock_idle_after_seconds > 0 and self.last_message_time > 0:
            if (now - self.last_message_time) < self.clock_idle_after_seconds:
                return

        current_second = int(now)
        if self.last_clock_second == current_second:
            return

        self.last_clock_second = current_second
        self._maybe_refresh_weather(now)
        clock_text = time.strftime(self.clock_format, time.localtime(now))
        self.scoreboard.display_clock(
            clock_text,
            color=self.clock_color,
            font_size=self.clock_font_size,
            right_text=self.weather_temperature_text,
            right_text_color=self.weather_color,
        )

    def _tick_rss_mode(self, now: float) -> None:
        """Refresh and render RSS ticker frames."""
        if self.scoreboard is None:
            return

        self._refresh_rss_if_due(now)

        if (now - self.rss_last_frame_time) < self.rss_frame_interval_seconds:
            return

        elapsed_seconds = (
            self.rss_frame_interval_seconds
            if self.rss_last_frame_time <= 0
            else max(0.0, now - self.rss_last_frame_time)
        )
        elapsed_seconds = min(
            elapsed_seconds,
            self.rss_frame_interval_seconds * self.rss_max_elapsed_factor,
        )

        self.rss_last_frame_time = now
        self.rss_scroll_px_float += self.rss_scroll_pixels_per_second * elapsed_seconds
        self.rss_scroll_px = int(self.rss_scroll_px_float)

        ticker_text = self.rss_ticker_text or self.rss_fallback_text or "RSS"
        self.scoreboard.display_ticker(
            ticker_text,
            scroll_px=self.rss_scroll_px,
            color=self.rss_color,
            font_size=self.rss_font_size,
            ticker_gap=self.rss_ticker_gap,
        )

    def _refresh_rss_if_due(self, now: float, force: bool = False) -> None:
        """Refresh RSS headlines at configured intervals."""
        if not self.rss_feed_url:
            self.rss_ticker_text = self.rss_fallback_text or "RSS: feed URL not set"
            return

        if not force and now < self.rss_next_fetch_time:
            return

        self.rss_next_fetch_time = now + self.rss_refresh_seconds

        headlines = self._fetch_rss_headlines()
        if headlines:
            self.rss_headlines = headlines
            self.rss_ticker_text = " | ".join(headlines)
            self.rss_scroll_px = 0
            self.rss_scroll_px_float = 0.0
            logger.info(f"Loaded {len(headlines)} RSS headlines")
        else:
            self.rss_ticker_text = self.rss_fallback_text or "RSS: no headlines"

    def _fetch_rss_headlines(self) -> List[str]:
        """Fetch headline titles from RSS or Atom feeds using stdlib XML parsing."""
        try:
            with urllib.request.urlopen(self.rss_feed_url, timeout=self.rss_timeout_seconds) as response:
                xml_payload = response.read()

            root = ET.fromstring(xml_payload)
            headlines: List[str] = []

            for item in root.findall(".//item"):
                title = item.findtext("title")
                cleaned = self._clean_headline(title)
                if cleaned:
                    headlines.append(cleaned)

            if not headlines:
                atom_namespace = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall(".//atom:entry", atom_namespace):
                    title = entry.findtext("atom:title", default="", namespaces=atom_namespace)
                    cleaned = self._clean_headline(title)
                    if cleaned:
                        headlines.append(cleaned)

            return headlines[:20]
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            logger.warning(f"Unable to refresh RSS headlines: {exc}")
            return []

    @staticmethod
    def _clean_headline(value: Optional[str]) -> str:
        """Normalize headline text for compact display on the ticker."""
        if value is None:
            return ""
        return " ".join(str(value).strip().split())

    def _handle_control_message(self, payload: Any) -> None:
        """Handle control messages used to switch modes and tune RSS settings."""
        try:
            if isinstance(payload, dict):
                self._apply_control_dict(payload)
                return

            raw_text = str(payload).strip()
            if not raw_text:
                return

            lowered = raw_text.lower()
            if lowered.startswith("mode:"):
                self._set_mode(raw_text.split(":", 1)[1], reason="mqtt control")
                return

            if lowered in self.VALID_MODES:
                self._set_mode(lowered, reason="mqtt control")
                return

            logger.warning(f"Unsupported control message: {payload}")
        except Exception as exc:
            logger.error(f"Error handling control message: {exc}", exc_info=True)

    def _apply_control_dict(self, payload: Dict[str, Any]) -> None:
        """Apply dictionary-form control payloads."""
        mode_request = payload.get("mode")
        if mode_request is None and payload.get("action") == "set_mode":
            mode_request = payload.get("value")
        if mode_request:
            self._set_mode(str(mode_request), reason="mqtt control")

        rss_url = payload.get("rss_feed_url") or payload.get("feed_url")
        if rss_url:
            self.rss_feed_url = str(rss_url).strip()
            self.rss_next_fetch_time = 0.0

        refresh_value = payload.get("rss_refresh_seconds") or payload.get("refresh_seconds")
        if refresh_value is not None:
            self.rss_refresh_seconds = self._as_int(refresh_value, fallback=self.rss_refresh_seconds, minimum=30)

        if bool(payload.get("rss_refresh_now", False)):
            self._refresh_rss_if_due(now=time.time(), force=True)

    def _set_mode(self, requested_mode: str, reason: str = "") -> None:
        """Switch active mode if valid."""
        normalized_mode = self._normalize_mode(requested_mode)
        if normalized_mode == self.active_mode:
            return

        previous_mode = self.active_mode
        self.active_mode = normalized_mode

        if normalized_mode == "rss":
            self.rss_scroll_px = 0
            self.rss_scroll_px_float = 0.0
            self.rss_last_frame_time = 0.0
            self._refresh_rss_if_due(now=time.time(), force=True)

        logger.info(
            f"Mode changed from '{previous_mode}' to '{normalized_mode}'"
            + (f" ({reason})" if reason else "")
        )

    def _normalize_mode(self, requested_mode: str) -> str:
        """Normalize unknown modes back to scoreboard mode."""
        normalized = str(requested_mode).strip().lower()
        if normalized in self.VALID_MODES:
            return normalized

        logger.warning(f"Unknown mode '{requested_mode}'. Falling back to 'scoreboard'.")
        return "scoreboard"

    @staticmethod
    def _as_int(value: Any, fallback: int, minimum: Optional[int] = None) -> int:
        """Parse int settings safely with optional lower bound."""
        try:
            parsed = int(value)
            if minimum is not None and parsed < minimum:
                return minimum
            return parsed
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _as_float(value: Any, fallback: float, minimum: Optional[float] = None) -> float:
        """Parse float settings safely with optional lower bound."""
        try:
            parsed = float(value)
            if minimum is not None and parsed < minimum:
                return minimum
            return parsed
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _as_optional_int(value: Any, minimum: int = 1) -> Optional[int]:
        """Parse optional integer values, returning None on invalid input."""
        if value is None:
            return None

        try:
            parsed = int(value)
            if parsed < minimum:
                return None
            return parsed
        except (TypeError, ValueError):
            return None

    def _maybe_refresh_weather(self, now: float) -> None:
        """Refresh cached weather data at the configured interval."""
        if not self.weather_enabled:
            return

        if now < self.weather_next_fetch_time:
            return

        self.weather_next_fetch_time = now + self.weather_refresh_seconds

        temperature = self._fetch_current_temperature()
        if temperature is not None:
            self.weather_temperature_text = temperature

    def _fetch_current_temperature(self) -> Optional[str]:
        """Fetch current temperature from Open-Meteo."""
        params = {
            "latitude": self.weather_latitude,
            "longitude": self.weather_longitude,
            "current": "temperature_2m",
            "temperature_unit": "fahrenheit" if self.weather_unit == "F" else "celsius",
            "timezone": "America/Indiana/Indianapolis",
        }
        url = f"https://api.open-meteo.com/v1/forecast?{urllib.parse.urlencode(params)}"

        try:
            with urllib.request.urlopen(url, timeout=self.weather_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))

            current = payload.get("current") or {}
            raw_temp = current.get("temperature_2m")
            if raw_temp is None:
                logger.warning("Weather response missing current.temperature_2m")
                return None

            rounded = int(round(float(raw_temp)))
            return f"{rounded}{self.weather_unit}"

        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"Unable to refresh weather temperature: {exc}")
            return None

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
