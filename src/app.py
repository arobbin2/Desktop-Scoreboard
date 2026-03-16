"""Main application - ties together MQTT and LED display"""

import logging
import socket
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
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

    VALID_MODES = {"scoreboard", "clock", "rss", "cubs", "crown"}

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
        log_level_name = str(app_config.get("log_level", "INFO")).strip().upper() or "INFO"
        log_level_value = getattr(logging, log_level_name, logging.INFO)
        logging.getLogger().setLevel(log_level_value)
        logger.setLevel(log_level_value)
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

        crown_config = self.config.get("crown") or {}
        self.crown_enabled = bool(crown_config.get("enabled", False))
        self.crown_meter_topic = str(crown_config.get("meter_topic", "scoreboard/crown/meter")).strip()
        self.crown_meter_topic_normalized = self.crown_meter_topic.lower()
        self.crown_udp_enabled = bool(crown_config.get("udp_enabled", True))
        self.crown_udp_bind_host = str(crown_config.get("udp_bind_host", "0.0.0.0")).strip() or "0.0.0.0"
        self.crown_udp_bind_port = self._as_int(crown_config.get("udp_bind_port"), fallback=10001, minimum=1)
        self.crown_subscribe_enabled = bool(crown_config.get("subscribe_enabled", True))
        self.crown_subscribe_host = str(crown_config.get("subscribe_host", "10.255.40.23")).strip()
        self.crown_subscribe_transport = str(crown_config.get("subscribe_transport", "udp")).strip().lower()
        if self.crown_subscribe_transport not in {"udp", "tcp"}:
            logger.warning("Invalid crown.subscribe_transport. Using udp.")
            self.crown_subscribe_transport = "udp"
        self.crown_subscribe_tcp_persistent = bool(crown_config.get("subscribe_tcp_persistent", True))
        self.crown_subscribe_port = self._as_int(crown_config.get("subscribe_port"), fallback=3804, minimum=1)
        self.crown_subscribe_interval_seconds = self._as_float(
            crown_config.get("subscribe_interval_seconds"),
            fallback=2.0,
            minimum=0.5,
        )
        self.crown_subscribe_payload_hex = str(
            crown_config.get(
                "subscribe_payload_hex",
                "02,19,00,00,00,2B,00,33,00,00,00,00,0E,EC,00,10,17,01,01,0F,00,20,05,00,00,00,01,00,00,00,00,33,00,10,17,01,00,00,00,00,00,00,00",
            )
        )
        self.crown_subscribe_payload_bytes = self._parse_hex_payload(self.crown_subscribe_payload_hex)
        self.crown_subscribe_use_subscribe_all_sensor = bool(
            crown_config.get("subscribe_use_subscribe_all_sensor", False)
        )
        self.crown_subscribe_sensor_change_type = self._as_int(
            crown_config.get("subscribe_sensor_change_type"),
            fallback=2,
            minimum=0,
        )
        self.crown_subscribe_sensor_rate_ms = self._as_int(
            crown_config.get("subscribe_sensor_rate_ms"),
            fallback=50,
            minimum=1,
        )
        self.crown_subscribe_initial_update = self._as_int(
            crown_config.get("subscribe_initial_update"),
            fallback=1,
            minimum=0,
        )
        inferred_source_node = self._infer_source_node_from_subscribe_payload(self.crown_subscribe_payload_bytes)
        self.crown_source_node = self._as_int(crown_config.get("source_node"), fallback=inferred_source_node, minimum=1)
        self.crown_tcp_keepalive_interval_seconds = self._as_float(
            crown_config.get("tcp_keepalive_interval_seconds"),
            fallback=5.0,
            minimum=1.0,
        )
        self.crown_udp_hex_byte_index = self._as_int(crown_config.get("udp_hex_byte_index"), fallback=0, minimum=0)
        self.crown_meter_min_db = self._as_float(crown_config.get("meter_min_db"), fallback=-60.0)
        self.crown_meter_max_db = self._as_float(crown_config.get("meter_max_db"), fallback=0.0)
        if self.crown_meter_max_db <= self.crown_meter_min_db:
            self.crown_meter_max_db = self.crown_meter_min_db + 1.0
        self.crown_marker_channel_id = self._as_int(
            crown_config.get("marker_channel_id"),
            fallback=1,
            minimum=0,
        )
        self.crown_marker_offset_db = self._as_float(
            crown_config.get("marker_offset_db"),
            fallback=48.0,
        )
        self.crown_frame_interval_seconds = self._as_float(
            crown_config.get("frame_interval_seconds"),
            fallback=0.1,
            minimum=0.02,
        )
        self.crown_stale_after_seconds = self._as_float(
            crown_config.get("stale_after_seconds"),
            fallback=5.0,
            minimum=0.5,
        )
        self.crown_fallback_to_scoreboard_on_stale = bool(
            crown_config.get("fallback_to_scoreboard_on_stale", True)
        )
        self.crown_waiting_text = str(crown_config.get("waiting_text", "CROWN WAIT")).strip() or "CROWN WAIT"

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

        cubs_config = self.config.get("cubs") or {}
        self.cubs_team_id = self._as_int(cubs_config.get("team_id"), fallback=112, minimum=1)
        self.cubs_timeout_seconds = self._as_float(cubs_config.get("timeout_seconds"), fallback=4.0, minimum=1.0)
        self.cubs_refresh_seconds = self._as_int(cubs_config.get("refresh_seconds"), fallback=15, minimum=5)
        self.cubs_off_day_text = str(cubs_config.get("off_day_text", "No Cubs Game Today")).strip()
        self.cubs_show_when_final = bool(cubs_config.get("show_when_final", True))

        self.cubs_state: Dict[str, Any] = {
            "away_team": "AWAY",
            "home_team": "HOME",
            "away_score": "-",
            "home_score": "-",
            "inning_text": "WAITING",
            "count_text": "B0 S0 O0",
            "bases_text": "BASES: ---",
            "status_text": self.cubs_off_day_text or "No Cubs Game",
        }
        self.cubs_next_fetch_time = 0.0
        self.cubs_last_render_key = ""

        self.crown_meter_state: Dict[str, Any] = {
            "levels": [],
            "updated_at": 0.0,
            "status_text": self.crown_waiting_text,
        }
        self.crown_last_payload_time = 0.0
        self.crown_last_render_key = ""
        self.crown_last_frame_time = 0.0
        self.crown_udp_socket: Optional[socket.socket] = None
        self.crown_tcp_socket: Optional[socket.socket] = None
        self.crown_udp_bound_address = ""
        self.crown_last_subscribe_time = 0.0
        self.crown_subscribe_send_count = 0
        self.crown_last_tcp_keepalive_time = 0.0
        self.crown_tcp_sequence = 1
        self.crown_subscribe_all_log_emitted = False
        self.crown_udp_packet_log_counter = 0
        self.crown_udp_unparsed_log_counter = 0
        self.crown_marker_last_raw_by_channel: Dict[int, float] = {}

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

            if self.active_mode == "cubs":
                self._refresh_cubs_if_due(now=time.time(), force=True)

            if self.active_mode == "crown" and not self.crown_enabled:
                self._set_mode("scoreboard", reason="crown disabled in config")
            elif self.active_mode == "crown" and self.crown_udp_enabled:
                self._ensure_crown_udp_socket()

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

        if normalized_topic == self.crown_meter_topic_normalized:
            self._handle_crown_meter_message(payload)
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

        if self.active_mode == "cubs":
            self._tick_cubs_mode(now)
            return

        if self.active_mode == "clock":
            self._maybe_render_clock(now, respect_idle=False)
            return

        if self.active_mode == "crown":
            self._tick_crown_mode(now)
            return

        self._maybe_render_clock(now, respect_idle=True)

    def _tick_crown_mode(self, now: float) -> None:
        """Render Crown meter mode and fall back when feed becomes stale."""
        if self.scoreboard is None:
            return

        if not self.crown_enabled:
            self._set_mode("scoreboard", reason="crown disabled in config")
            return

        if self.crown_udp_enabled:
            self._ensure_crown_udp_socket()
            self._maybe_send_crown_subscribe(now)
            self._maybe_send_crown_tcp_keepalive(now)
            self._poll_crown_udp_packets()

        if (now - self.crown_last_frame_time) < self.crown_frame_interval_seconds:
            return

        self.crown_last_frame_time = now

        if self.crown_last_payload_time <= 0:
            render_text = self.crown_waiting_text
        else:
            stale_seconds = now - self.crown_last_payload_time
            if stale_seconds > self.crown_stale_after_seconds:
                if self.crown_fallback_to_scoreboard_on_stale:
                    self._set_mode("scoreboard", reason="crown feed stale")
                    return
                render_text = "CROWN STALE"
            else:
                render_text = self._format_crown_levels_text(self.crown_meter_state.get("levels") or [])

        levels = self.crown_meter_state.get("levels") or []
        has_meter_level = len(levels) > 0
        meter_level = float(levels[0]) if has_meter_level else 0.0
        render_key = f"meter:{meter_level:.2f}" if has_meter_level else f"text:{render_text}"

        if render_key == self.crown_last_render_key:
            return

        self.crown_last_render_key = render_key
        if has_meter_level and hasattr(self.scoreboard, "display_single_meter"):
            self.scoreboard.display_single_meter(
                meter_level,
                label="CROWN",
                min_db=self.crown_meter_min_db,
                max_db=self.crown_meter_max_db,
                color=(0, 255, 255),
            )
        else:
            self.scoreboard.display_text(render_text, color=(0, 255, 255))

    def _ensure_crown_udp_socket(self) -> None:
        """Create a non-blocking UDP socket for Crown meter data if needed."""
        if self.crown_udp_socket is not None:
            return

        try:
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_socket.bind((self.crown_udp_bind_host, self.crown_udp_bind_port))
            udp_socket.setblocking(False)
            self.crown_udp_socket = udp_socket
            self.crown_udp_bound_address = f"{self.crown_udp_bind_host}:{self.crown_udp_bind_port}"
            logger.info(f"Crown UDP listener bound on {self.crown_udp_bound_address}")
        except OSError as exc:
            self.crown_udp_socket = None
            logger.warning(
                f"Unable to bind Crown UDP listener on {self.crown_udp_bind_host}:{self.crown_udp_bind_port}: {exc}"
            )

    def _poll_crown_udp_packets(self) -> None:
        """Read available UDP packets and update current Crown meter level."""
        if self.crown_udp_socket is None:
            return

        # Drain up to 20 packets per tick so the display uses the latest meter value.
        # Meter frames arrive at ~50 ms intervals; draining more prevents queue build-up.
        for _ in range(20):
            try:
                payload, addr = self.crown_udp_socket.recvfrom(65535)
            except BlockingIOError:
                return
            except OSError as exc:
                logger.warning(f"Crown UDP read error: {exc}")
                return

            self.crown_udp_packet_log_counter += 1
            # Avoid flooding logs while still proving packet flow is alive.
            if self.crown_udp_packet_log_counter % 100 == 1:
                logger.debug("Crown UDP packet: src=%s:%d len=%d", addr[0], addr[1], len(payload))
            level = self._extract_udp_meter_level(payload)
            if level is None:
                self.crown_udp_unparsed_log_counter += 1
                if self.crown_udp_unparsed_log_counter % 200 == 1:
                    preview = payload[:32].hex()
                    logger.debug("Crown UDP unparsed sample: len=%d head=%s", len(payload), preview)
                continue

            now = time.time()
            self.crown_meter_state = {
                "levels": [level],
                "updated_at": now,
                "status_text": "udp-live",
            }
            self.crown_last_payload_time = now

    def _maybe_send_crown_subscribe(self, now: float, force: bool = False) -> None:
        """Send Crown subscribe packet using configured transport (udp|tcp)."""
        if not self.crown_subscribe_enabled:
            return

        if not self.crown_subscribe_host:
            return

        if (not force) and self.crown_last_subscribe_time > 0:
            elapsed = now - self.crown_last_subscribe_time
            if elapsed < self.crown_subscribe_interval_seconds:
                return

        payload_bytes = self._resolve_crown_subscribe_payload()
        if not payload_bytes:
            return

        sent = False
        if self.crown_subscribe_transport == "tcp":
            sent = self._send_crown_subscribe_tcp(payload_bytes)
        else:
            sent = self._send_crown_subscribe_udp(payload_bytes)

        if sent:
            self.crown_last_subscribe_time = now
            self.crown_subscribe_send_count += 1

    def _send_crown_subscribe_udp(self, payload_bytes: bytes) -> bool:
        """Send subscribe payload over UDP from the bound listener socket."""
        if self.crown_udp_socket is None:
            return False

        try:
            self.crown_udp_socket.sendto(
                payload_bytes,
                (self.crown_subscribe_host, self.crown_subscribe_port),
            )
            if self.crown_subscribe_send_count == 0:
                logger.info(
                    "Sent Crown subscribe packet via UDP to "
                    f"{self.crown_subscribe_host}:{self.crown_subscribe_port} "
                    f"from local UDP {self.crown_udp_bind_port}"
                )
            return True
        except OSError as exc:
            logger.warning(
                "Unable to send Crown subscribe packet via UDP to "
                f"{self.crown_subscribe_host}:{self.crown_subscribe_port}: {exc}"
            )
            return False

    def _send_crown_subscribe_tcp(self, payload_bytes: bytes) -> bool:
        """Send subscribe payload over TCP, reusing a socket when configured."""
        tcp_socket = self._ensure_crown_tcp_socket()
        if tcp_socket is None:
            return False

        try:
            tcp_socket.sendall(payload_bytes)
            if self.crown_subscribe_send_count == 0:
                logger.info(
                    "Sent Crown subscribe packet via TCP to "
                    f"{self.crown_subscribe_host}:{self.crown_subscribe_port}"
                )

            self._poll_crown_tcp_responses(max_frames=4)

            if not self.crown_subscribe_tcp_persistent:
                self._close_crown_tcp_socket()

            return True
        except OSError as exc:
            logger.warning(
                "Unable to send Crown subscribe packet via TCP to "
                f"{self.crown_subscribe_host}:{self.crown_subscribe_port}: {exc}"
            )
            self._close_crown_tcp_socket()
            return False

    def _ensure_crown_tcp_socket(self) -> Optional[socket.socket]:
        """Connect TCP control socket for Crown subscribe traffic."""
        if self.crown_tcp_socket is not None:
            return self.crown_tcp_socket

        try:
            tcp_socket = socket.create_connection((self.crown_subscribe_host, self.crown_subscribe_port), timeout=1.0)
            tcp_socket.settimeout(None)
            self.crown_tcp_socket = tcp_socket
            logger.info(
                "Connected Crown TCP control socket to "
                f"{self.crown_subscribe_host}:{self.crown_subscribe_port}"
            )
            self._send_crown_discovery_message(info_bit=False)
            self.crown_last_tcp_keepalive_time = time.time()
            self._poll_crown_tcp_responses(max_frames=2)
            return tcp_socket
        except OSError as exc:
            logger.warning(
                "Unable to connect Crown TCP control socket to "
                f"{self.crown_subscribe_host}:{self.crown_subscribe_port}: {exc}"
            )
            self.crown_tcp_socket = None
            return None

    def _close_crown_tcp_socket(self) -> None:
        """Close TCP socket used for Crown subscribe traffic."""
        if self.crown_tcp_socket is None:
            return

        try:
            self.crown_tcp_socket.close()
        except OSError:
            pass
        finally:
            self.crown_tcp_socket = None

    def _maybe_send_crown_tcp_keepalive(self, now: float) -> None:
        """Send HiQnet keepalive over TCP while connected in crown mode."""
        if self.crown_subscribe_transport != "tcp":
            return

        if self.crown_tcp_socket is None:
            return

        if (now - self.crown_last_tcp_keepalive_time) < self.crown_tcp_keepalive_interval_seconds:
            return

        if self._send_crown_discovery_message(info_bit=True):
            self.crown_last_tcp_keepalive_time = now

        self._poll_crown_tcp_responses(max_frames=3)

    def _poll_crown_tcp_responses(self, max_frames: int = 4) -> None:
        """Read and process a few pending TCP control frames from the Crown device."""
        if self.crown_tcp_socket is None:
            return

        for _ in range(max_frames):
            try:
                self.crown_tcp_socket.settimeout(0.01)
                response = self.crown_tcp_socket.recv(4096)
            except TimeoutError:
                return
            except OSError:
                return
            finally:
                if self.crown_tcp_socket is not None:
                    self.crown_tcp_socket.settimeout(None)

            if not response:
                return

            self._handle_crown_tcp_response(response)

    def _handle_crown_tcp_response(self, payload: bytes) -> None:
        """Handle HiQnet TCP frames; currently logs headers and refuses Hello sessions."""
        if len(payload) < 25 or payload[0] != 0x02:
            logger.debug("Crown TCP response: len=%d", len(payload))
            return

        header_len = payload[1]
        if header_len < 25 or len(payload) < header_len:
            logger.debug("Crown TCP malformed header: len=%d header_len=%d", len(payload), header_len)
            return

        msg_id = int.from_bytes(payload[18:20], "big")
        flags = int.from_bytes(payload[20:22], "big")
        seq = int.from_bytes(payload[23:25], "big")
        logger.debug(
            "Crown TCP response: len=%d msg_id=0x%04X flags=0x%04X seq=%d",
            len(payload),
            msg_id,
            flags,
            seq,
        )

        # Message ID 0x0008 is HiQnet Hello session request; refuse session to stay session-less.
        if msg_id == 0x0008:
            self._send_crown_hello_refusal(payload)

    def _send_crown_hello_refusal(self, hello_payload: bytes) -> None:
        """Reply to a HiQnet Hello request with an error header to refuse sessions."""
        if self.crown_tcp_socket is None or len(hello_payload) < 25:
            return

        seq = hello_payload[23:25]
        source_addr = hello_payload[12:18]  # swap src/dst
        destination_addr = hello_payload[6:12]

        response = bytearray()
        response.extend([0x02, 0x1D])  # version=2, header_len=29
        response.extend((29).to_bytes(4, "big"))
        response.extend(source_addr)
        response.extend(destination_addr)
        response.extend((0x0008).to_bytes(2, "big"))
        response.extend((0x002C).to_bytes(2, "big"))  # guaranteed + error + info
        response.extend((5).to_bytes(1, "big"))
        response.extend(seq)
        response.extend((2).to_bytes(2, "big"))  # error length
        response.extend((0).to_bytes(2, "big"))  # error code 0

        try:
            self.crown_tcp_socket.sendall(bytes(response))
            logger.debug("Sent Crown Hello refusal (session-less mode)")
        except OSError as exc:
            logger.warning(f"Unable to send Crown Hello refusal: {exc}")

    def _send_crown_discovery_message(self, info_bit: bool) -> bool:
        """Send HiQnet Discovery (or KeepAlive when info_bit=True) over TCP."""
        if self.crown_tcp_socket is None:
            return False

        flags = 0x0024 if info_bit else 0x0020

        payload = bytearray()
        payload.extend(int(self.crown_source_node).to_bytes(2, "big"))  # node
        payload.extend((1).to_bytes(1, "big"))  # cost
        payload.extend((16).to_bytes(2, "big"))  # serial length
        payload.extend(self._build_discovery_serial_16())  # serial (16 bytes)
        payload.extend((1048576).to_bytes(4, "big"))  # max message size
        payload.extend((10000).to_bytes(2, "big"))  # keepalive ms
        payload.extend((1).to_bytes(1, "big"))  # network id
        payload.extend(bytes([0, 0, 0, 0, 0, 0]))  # mac
        payload.extend((1).to_bytes(1, "big"))  # dhcp
        payload.extend(bytes([0, 0, 0, 0]))  # ip
        payload.extend(bytes([0, 0, 0, 0]))  # subnet
        payload.extend(bytes([0, 0, 0, 0]))  # gateway

        frame = bytearray()
        frame.extend([0x02, 0x19])  # version=2, header_len=25
        frame.extend((72).to_bytes(4, "big"))  # total message length
        frame.extend(int(self.crown_source_node).to_bytes(2, "big"))
        frame.extend((0).to_bytes(4, "big"))
        frame.extend((0).to_bytes(2, "big"))  # discovery destination node
        frame.extend((0).to_bytes(4, "big"))
        frame.extend((0x0000).to_bytes(2, "big"))  # discovery msg id
        frame.extend(flags.to_bytes(2, "big"))
        frame.extend((5).to_bytes(1, "big"))
        frame.extend((self.crown_tcp_sequence & 0xFFFF).to_bytes(2, "big"))
        frame.extend(payload)

        self.crown_tcp_sequence = (self.crown_tcp_sequence + 1) & 0xFFFF

        try:
            self.crown_tcp_socket.sendall(bytes(frame))
            if info_bit:
                logger.debug("Sent Crown TCP keepalive discovery frame")
            else:
                logger.debug("Sent Crown TCP discovery frame")
            return True
        except OSError as exc:
            logger.warning(f"Unable to send Crown TCP discovery frame: {exc}")
            return False

    def _build_discovery_serial_16(self) -> bytes:
        """Build a stable 16-byte serial value for HiQnet discovery payload."""
        serial = bytearray(16)
        source_node = int(self.crown_source_node) & 0xFFFF
        serial[-2:] = source_node.to_bytes(2, "big")
        return bytes(serial)

    def _resolve_crown_subscribe_payload(self) -> bytes:
        """Return configured payload or generated SubscribeAll-sensor payload."""
        if self.crown_subscribe_use_subscribe_all_sensor:
            generated = self._build_subscribe_all_sensor_payload()
            if generated:
                return generated
        return self.crown_subscribe_payload_bytes

    def _build_subscribe_all_sensor_payload(self) -> bytes:
        """Build HiQnet ParamSubscribeAll (0x0113) from configured source/destination addresses."""
        base = self.crown_subscribe_payload_bytes
        if len(base) < 25 or base[0] != 0x02:
            return b""

        source_address = bytes(base[6:12])
        destination_address = bytes(base[12:18])
        destination_vd_object = bytes(base[14:18])

        frame = bytearray()
        frame.extend([0x02, 0x19])
        frame.extend((36).to_bytes(4, "big"))
        frame.extend(source_address)
        frame.extend(destination_address)
        frame.extend((0x0113).to_bytes(2, "big"))
        frame.extend((0x0020).to_bytes(2, "big"))
        frame.extend((5).to_bytes(1, "big"))
        frame.extend((self.crown_tcp_sequence & 0xFFFF).to_bytes(2, "big"))
        frame.extend((int(self.crown_source_node) & 0xFFFF).to_bytes(2, "big"))
        frame.extend(destination_vd_object)
        frame.extend((int(self.crown_subscribe_sensor_change_type) & 0xFF).to_bytes(1, "big"))
        frame.extend((int(self.crown_subscribe_sensor_rate_ms) & 0xFFFF).to_bytes(2, "big"))
        frame.extend((int(self.crown_subscribe_initial_update) & 0xFFFF).to_bytes(2, "big"))
        self.crown_tcp_sequence = (self.crown_tcp_sequence + 1) & 0xFFFF

        if not self.crown_subscribe_all_log_emitted:
            logger.debug(
                "Built Crown SubscribeAll-sensor payload: source_node=%d change_type=%d sensor_rate_ms=%d initial_update=%d",
                self.crown_source_node,
                self.crown_subscribe_sensor_change_type,
                self.crown_subscribe_sensor_rate_ms,
                self.crown_subscribe_initial_update,
            )
            self.crown_subscribe_all_log_emitted = True
        return bytes(frame)

    def _extract_udp_meter_level(self, payload: bytes) -> Optional[float]:
        """Parse a HiQnet MultiParamSet UDP frame and return the first parameter value.

        Sensor parameters (meters) arrive as MultiParamSet messages (msg ID 0x0100)
        on UDP port 3804.  Non-MultiParamSet frames (e.g. Discovery/KeepAlive at 72
        bytes, msg ID 0x0000) are silently ignored.

        HiQnet header layout (25 bytes):
          [0]      version (0x02)
          [1]      header length (0x19 = 25)
          [2-5]    message length
          [6-7]    source NODE
          [8-11]   source VD-OBJECT
          [12-13]  destination NODE
          [14-17]  destination VD-OBJECT
          [18-19]  message ID (big-endian)
          [20-21]  flags
          [22]     hop count
          [23-24]  sequence number
        MultiParamSet payload (starting at byte header_len):
          [+0..+1] number of parameters
          [+2..+3] parameter ID
          [+4]     datatype  (6 = FLOAT32, 4 = LONG, 5 = ULONG)
          [+5..+8] value (4 bytes for 32-bit types)
        """
        import struct

        # Some devices publish raw FLOAT32 sensor values as 4-byte UDP payloads.
        if len(payload) == 4:
            try:
                raw_short = struct.unpack(">f", payload)[0]
            except struct.error:
                raw_short = float("nan")

            if raw_short == raw_short and -200.0 <= raw_short <= 50.0:
                logger.debug("Crown UDP raw FLOAT32 fallback: %.3f", raw_short)
                return max(self.crown_meter_min_db, min(self.crown_meter_max_db, raw_short))

            logger.debug("Crown UDP short payload ignored: len=4 value=%r", payload)
            return None

        marker_level = self._extract_marker_float_level(payload)
        if marker_level is not None:
            return marker_level

        # Try as a direct frame first.
        direct_level = self._extract_hiqnet_level_from_frame(payload, 0)
        if direct_level is not None:
            return direct_level

        # Some devices batch/encapsulate multiple frames in one datagram; scan for version byte.
        # Header length is not always 0x19, so we try each plausible frame start.
        scan_limit = max(0, len(payload) - 25)
        for marker in range(scan_limit + 1):
            if payload[marker] != 0x02:
                continue
            embedded_level = self._extract_hiqnet_level_from_frame(payload, marker)
            if embedded_level is not None:
                return embedded_level

        if len(payload) < 34:
            logger.debug("Crown UDP short payload ignored: len=%d", len(payload))
        return None

    def _extract_marker_float_level(self, payload: bytes) -> Optional[float]:
        """Extract Crown meter using known marker pattern: 10 17 <ch> ... 0b 06 <float32>."""
        import struct

        search = 0
        candidates: List[Tuple[int, float, float]] = []
        while True:
            marker = payload.find(b"\x10\x17", search)
            if marker < 0:
                break

            if (marker + 2) >= len(payload):
                break
            channel_id = int(payload[marker + 2])
            if self.crown_marker_channel_id > 0 and channel_id != self.crown_marker_channel_id:
                search = marker + 1
                continue

            # Search for 0B 06 near channel marker and parse following big-endian float.
            end = min(len(payload), marker + 128)
            field = payload.find(b"\x0b\x06", marker, end)
            if field != -1 and (field + 6) <= len(payload):
                try:
                    raw = float(struct.unpack(">f", payload[field + 2 : field + 6])[0])
                except (struct.error, ValueError, OverflowError):
                    raw = float("nan")

                if raw == raw and -200.0 <= raw <= 50.0:
                    # Many Crown marker values are in an absolute scale (~41..48).
                    # Convert to display dBFS-like scale using configurable offset.
                    mapped_source = raw
                    if raw > self.crown_meter_max_db:
                        mapped_source = raw - self.crown_marker_offset_db

                    mapped = max(self.crown_meter_min_db, min(self.crown_meter_max_db, mapped_source))
                    candidates.append((channel_id, raw, mapped))

            search = marker + 1

        if not candidates:
            return None

        if self.crown_marker_channel_id > 0:
            # Fixed channel mode: pick hottest value for configured channel.
            selected = max(candidates, key=lambda item: item[2])
        else:
            # Auto mode: pick channel with highest short-term movement to surface active meter.
            selected = candidates[0]
            best_score = -1.0
            for channel_id, raw, mapped in candidates:
                previous = self.crown_marker_last_raw_by_channel.get(channel_id)
                score = abs(raw - previous) if previous is not None else 0.0
                if score > best_score:
                    best_score = score
                    selected = (channel_id, raw, mapped)

            for channel_id, raw, _mapped in candidates:
                self.crown_marker_last_raw_by_channel[channel_id] = raw

        logger.debug(
            "Crown marker FLOAT32: ch=%d raw=%.3f mapped=%.3f",
            selected[0],
            selected[1],
            selected[2],
        )
        return selected[2]

    def _extract_hiqnet_level_from_frame(self, payload: bytes, offset: int) -> Optional[float]:
        """Attempt to extract one meter value from a HiQnet MultiParamSet frame at offset."""
        import struct

        if len(payload) < (offset + 25):
            return None
        if payload[offset] != 0x02:  # HiQnet version 2
            return None

        header_len = payload[offset + 1]
        if header_len < 25 or len(payload) < (offset + header_len + 3):
            return None

        msg_id = struct.unpack(">H", payload[offset + 18 : offset + 20])[0]
        if msg_id != 0x0100:
            return None

        message_len = struct.unpack(">I", payload[offset + 2 : offset + 6])[0]
        frame_end = min(len(payload), offset + max(header_len, message_len))

        p = offset + header_len
        if p + 2 > frame_end:
            return None
        num_params = struct.unpack(">H", payload[p : p + 2])[0]
        if num_params <= 0:
            return None
        p += 2

        for _ in range(num_params):
            if p + 3 > frame_end:
                break

            param_id = struct.unpack(">H", payload[p : p + 2])[0]
            datatype = payload[p + 2]
            p += 3

            value_size = self._hiqnet_datatype_size(datatype)
            if value_size <= 0 or (p + value_size) > frame_end:
                break

            raw = self._decode_hiqnet_numeric_value(payload[p : p + value_size], datatype)
            p += value_size
            if raw is None:
                continue

            meter_value = self._map_hiqnet_raw_to_meter_db(raw, datatype)
            logger.debug(
                "Crown UDP MultiParamSet: param_id=%d datatype=%d raw=%.6f mapped=%.3f",
                param_id,
                datatype,
                raw,
                meter_value,
            )
            return meter_value

        return None

    @staticmethod
    def _hiqnet_datatype_size(datatype: int) -> int:
        """Return byte width for HiQnet datatype enum."""
        if datatype in {0, 1}:  # BYTE, UBYTE
            return 1
        if datatype in {2, 3}:  # WORD, UWORD
            return 2
        if datatype in {4, 5, 6}:  # LONG, ULONG, FLOAT32
            return 4
        if datatype == 7:  # FLOAT64
            return 8
        return 0

    @staticmethod
    def _decode_hiqnet_numeric_value(raw_bytes: bytes, datatype: int) -> Optional[float]:
        """Decode HiQnet numeric payload bytes to float."""
        import struct

        try:
            if datatype == 0:
                return float(struct.unpack(">b", raw_bytes)[0])
            if datatype == 1:
                return float(struct.unpack(">B", raw_bytes)[0])
            if datatype == 2:
                return float(struct.unpack(">h", raw_bytes)[0])
            if datatype == 3:
                return float(struct.unpack(">H", raw_bytes)[0])
            if datatype == 4:
                return float(struct.unpack(">l", raw_bytes)[0])
            if datatype == 5:
                return float(struct.unpack(">L", raw_bytes)[0])
            if datatype == 6:
                return float(struct.unpack(">f", raw_bytes)[0])
            if datatype == 7:
                return float(struct.unpack(">d", raw_bytes)[0])
        except (struct.error, OverflowError, ValueError):
            return None
        return None

    def _map_hiqnet_raw_to_meter_db(self, raw_value: float, datatype: int) -> float:
        """Map decoded HiQnet numeric value to display dB range."""
        # FLOAT32/FLOAT64 are usually direct dB values.
        if datatype in {6, 7}:
            return max(self.crown_meter_min_db, min(self.crown_meter_max_db, raw_value))

        # If integer already looks like dB, trust it.
        if self.crown_meter_min_db <= raw_value <= self.crown_meter_max_db:
            return raw_value

        span = self.crown_meter_max_db - self.crown_meter_min_db
        if span <= 0:
            return self.crown_meter_min_db

        # Scale unsigned integer domains into dB span as a fallback.
        unsigned_max_by_type = {1: 255.0, 3: 65535.0, 5: 4294967295.0}
        max_raw = unsigned_max_by_type.get(datatype)
        if max_raw is not None and max_raw > 0:
            scaled = self.crown_meter_min_db + ((raw_value / max_raw) * span)
            return max(self.crown_meter_min_db, min(self.crown_meter_max_db, scaled))

        # Signed integers fallback: normalize full signed range to [0,1].
        signed_range_by_type = {
            0: (-128.0, 127.0),
            2: (-32768.0, 32767.0),
            4: (-2147483648.0, 2147483647.0),
        }
        signed_range = signed_range_by_type.get(datatype)
        if signed_range is not None:
            min_raw, max_raw_signed = signed_range
            normalized = (raw_value - min_raw) / (max_raw_signed - min_raw)
            normalized = max(0.0, min(1.0, normalized))
            scaled = self.crown_meter_min_db + (normalized * span)
            return max(self.crown_meter_min_db, min(self.crown_meter_max_db, scaled))

        return max(self.crown_meter_min_db, min(self.crown_meter_max_db, raw_value))

    @staticmethod
    def _extract_hex_bytes_from_udp_payload(payload: bytes) -> List[int]:
        """Parse UDP payload as either binary bytes or ASCII hex byte tokens."""
        if not payload:
            return []

        try:
            text = payload.decode("ascii").strip()
        except UnicodeDecodeError:
            text = ""

        if text:
            # Accept forms like '7F', '0x7F', '7F 1A', or '7F,1A'.
            normalized = text.replace(",", " ").replace("0x", "").replace("0X", "")
            tokens = [token.strip() for token in normalized.split() if token.strip()]
            if tokens:
                parsed: List[int] = []
                for token in tokens:
                    if len(token) > 2:
                        parsed = []
                        break
                    try:
                        parsed.append(int(token, 16))
                    except ValueError:
                        parsed = []
                        break
                if parsed:
                    return [value & 0xFF for value in parsed]

            compact = "".join(character for character in normalized if character in "0123456789abcdefABCDEF")
            if compact and (len(compact) % 2 == 0):
                try:
                    return [int(compact[index : index + 2], 16) for index in range(0, len(compact), 2)]
                except ValueError:
                    pass

        return [int(value) & 0xFF for value in payload]

    @staticmethod
    def _parse_hex_payload(raw_value: str) -> bytes:
        """Parse a comma/space-delimited hex string into bytes."""
        text = str(raw_value).strip()
        if not text:
            return b""

        normalized = text.replace("0x", "").replace("0X", "")
        normalized = normalized.replace(",", " ").replace(";", " ")
        compact = "".join(normalized.split())
        if not compact:
            return b""

        if len(compact) % 2 != 0:
            logger.warning("Invalid crown.subscribe_payload_hex length; expected even number of hex chars")
            return b""

        try:
            return bytes.fromhex(compact)
        except ValueError:
            logger.warning("Invalid crown.subscribe_payload_hex; unable to parse hex bytes")
            return b""

    @staticmethod
    def _infer_source_node_from_subscribe_payload(payload: bytes) -> int:
        """Infer HiQnet source node from message bytes, defaulting to 51."""
        if len(payload) >= 8 and payload[0] == 0x02 and payload[1] >= 0x19:
            return int.from_bytes(payload[6:8], "big") or 51
        return 51

    @staticmethod
    def _format_crown_levels_text(levels: List[float]) -> str:
        """Create a compact display string from channel dB levels."""
        if not levels:
            return "CROWN WAIT"

        segments: List[str] = []
        for index, level in enumerate(levels[:4], start=1):
            rounded = int(round(float(level)))
            segments.append(f"C{index}:{rounded}")
        return " ".join(segments)

    def _handle_crown_meter_message(self, payload: Any) -> None:
        """Ingest Crown meter payloads regardless of current active mode."""
        parsed_payload: Any = payload

        if isinstance(parsed_payload, str):
            stripped = parsed_payload.strip()
            if not stripped:
                return
            try:
                parsed_payload = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("Ignoring non-JSON Crown meter payload")
                return

        levels = self._extract_crown_levels(parsed_payload)
        if not levels:
            logger.warning("Crown payload did not contain usable channel levels")
            return

        now = time.time()
        self.crown_meter_state = {
            "levels": levels,
            "updated_at": now,
            "status_text": "live",
        }
        self.crown_last_payload_time = now

    def _extract_crown_levels(self, payload: Any) -> List[float]:
        """Extract a level list from supported Crown payload shapes."""
        if isinstance(payload, list):
            return self._coerce_level_list(payload)

        if not isinstance(payload, dict):
            return []

        if isinstance(payload.get("channels"), list):
            channels = payload.get("channels") or []
            extracted: List[Any] = []
            for channel in channels:
                if isinstance(channel, dict):
                    if "db" in channel:
                        extracted.append(channel.get("db"))
                    elif "level" in channel:
                        extracted.append(channel.get("level"))
                    elif "value" in channel:
                        extracted.append(channel.get("value"))
                else:
                    extracted.append(channel)
            levels = self._coerce_level_list(extracted)
            if levels:
                return levels

        if isinstance(payload.get("meters"), list):
            levels = self._coerce_level_list(payload.get("meters") or [])
            if levels:
                return levels

        stereo_candidates = [payload.get("left"), payload.get("right")]
        stereo_levels = self._coerce_level_list(stereo_candidates)
        if stereo_levels:
            return stereo_levels

        return []

    @staticmethod
    def _coerce_level_list(raw_values: List[Any]) -> List[float]:
        """Convert a list of raw values into bounded float dB levels."""
        parsed: List[float] = []
        for value in raw_values:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue

            parsed.append(min(20.0, max(-120.0, numeric)))

        return parsed

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

    def _tick_cubs_mode(self, now: float) -> None:
        """Refresh Cubs game state and render only when it changes."""
        if self.scoreboard is None:
            return

        self._refresh_cubs_if_due(now)
        render_key = json.dumps(self.cubs_state, sort_keys=True)
        if render_key == self.cubs_last_render_key:
            return

        self.cubs_last_render_key = render_key
        self.scoreboard.display_baseball_game(self.cubs_state)

    def _refresh_cubs_if_due(self, now: float, force: bool = False) -> None:
        """Refresh Cubs live game snapshot at configured intervals."""
        if not force and now < self.cubs_next_fetch_time:
            return

        self.cubs_next_fetch_time = now + self.cubs_refresh_seconds
        next_state = self._fetch_cubs_game_state()
        if next_state:
            self.cubs_state = next_state

    def _fetch_cubs_game_state(self) -> Dict[str, Any]:
        """Fetch current Cubs game info from MLB Stats API."""
        local_today = datetime.now().date()
        start_date = (local_today - timedelta(days=1)).isoformat()
        end_date = (local_today + timedelta(days=1)).isoformat()
        schedule_params = {
            "sportId": 1,
            "teamId": self.cubs_team_id,
            "startDate": start_date,
            "endDate": end_date,
            "hydrate": "team",
        }
        schedule_url = (
            "https://statsapi.mlb.com/api/v1/schedule?"
            + urllib.parse.urlencode(schedule_params)
        )

        try:
            with urllib.request.urlopen(schedule_url, timeout=self.cubs_timeout_seconds) as response:
                schedule_payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"Unable to refresh Cubs schedule: {exc}")
            return {
                **self.cubs_state,
                "status_text": "MLB API Unavailable",
            }

        game = self._select_preferred_cubs_game(schedule_payload)
        if not game:
            return {
                "away_team": "CUBS",
                "home_team": "-",
                "away_score": "-",
                "home_score": "-",
                "inning_text": "OFF DAY",
                "count_text": "B0 S0 O0",
                "bases_text": "BASES: ---",
                "status_text": self.cubs_off_day_text or "No Cubs Game",
            }

        game_pk = game.get("gamePk")
        if not game_pk:
            return {
                **self.cubs_state,
                "status_text": "Missing Game Data",
            }

        live_url = f"https://statsapi.mlb.com/api/v1.1/game/{int(game_pk)}/feed/live"
        try:
            with urllib.request.urlopen(live_url, timeout=self.cubs_timeout_seconds) as response:
                live_payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"Unable to refresh Cubs live feed: {exc}")
            return {
                **self.cubs_state,
                "status_text": "Live Feed Error",
            }

        return self._build_cubs_display_state(live_payload)

    def _select_preferred_cubs_game(self, schedule_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Select the most relevant Cubs game from schedule payload."""
        all_games: List[Dict[str, Any]] = []
        for date_block in schedule_payload.get("dates", []):
            games = date_block.get("games") or []
            for game in games:
                if isinstance(game, dict):
                    all_games.append(game)

        if not all_games:
            return None

        live_games = [
            game
            for game in all_games
            if str((game.get("status") or {}).get("abstractGameState", "")).lower() == "live"
        ]
        if live_games:
            return live_games[0]

        if self.cubs_show_when_final:
            final_games = [
                game
                for game in all_games
                if str((game.get("status") or {}).get("abstractGameState", "")).lower() == "final"
            ]
            if final_games:
                return final_games[-1]

        now = datetime.now(timezone.utc)

        def game_time_distance_seconds(game: Dict[str, Any]) -> float:
            raw = game.get("gameDate")
            if not raw:
                return float("inf")
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                return abs((parsed - now).total_seconds())
            except ValueError:
                return float("inf")

        return min(all_games, key=game_time_distance_seconds)

    def _build_cubs_display_state(self, live_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Build compact display state from MLB live feed payload."""
        game_data = live_payload.get("gameData") or {}
        live_data = live_payload.get("liveData") or {}
        linescore = live_data.get("linescore") or {}
        status = game_data.get("status") or {}

        teams = game_data.get("teams") or {}
        away_team = ((teams.get("away") or {}).get("abbreviation") or "AWAY").upper()[:4]
        home_team = ((teams.get("home") or {}).get("abbreviation") or "HOME").upper()[:4]

        score_teams = linescore.get("teams") or {}
        away_score = str(((score_teams.get("away") or {}).get("runs", "-")))[:2]
        home_score = str(((score_teams.get("home") or {}).get("runs", "-")))[:2]

        inning_state = str(linescore.get("inningState") or "").strip().upper()
        current_inning = str(linescore.get("currentInning") or "-")
        if inning_state in {"TOP", "BOTTOM", "MID", "END"}:
            inning_text = f"{inning_state} {current_inning}"
        else:
            short_state = (inning_state[:3] + " ") if inning_state else ""
            inning_text = f"{short_state}{current_inning}".strip()[:10]

        balls = int(linescore.get("balls") or 0)
        strikes = int(linescore.get("strikes") or 0)
        outs = int(linescore.get("outs") or 0)
        count_text = f"B{balls} S{strikes} O{outs}"

        offense = linescore.get("offense") or {}
        on_first = bool(offense.get("first"))
        on_second = bool(offense.get("second"))
        on_third = bool(offense.get("third"))

        if on_first and on_second and on_third:
            bases_text = "BASES: LOADED"
        else:
            occupied: List[str] = []
            if on_first:
                occupied.append("1B")
            if on_second:
                occupied.append("2B")
            if on_third:
                occupied.append("3B")
            bases_text = "BASES: " + (" ".join(occupied) if occupied else "---")

        status_text = str(status.get("detailedState") or status.get("abstractGameState") or "")

        return {
            "away_team": away_team,
            "home_team": home_team,
            "away_score": away_score,
            "home_score": home_score,
            "inning_text": inning_text,
            "count_text": count_text,
            "bases_text": bases_text,
            "status_text": status_text,
        }

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
        if "crown_enabled" in payload:
            self.crown_enabled = bool(payload.get("crown_enabled"))

        crown_topic = payload.get("crown_meter_topic")
        if crown_topic:
            self.crown_meter_topic = str(crown_topic).strip()
            self.crown_meter_topic_normalized = self.crown_meter_topic.lower()

        crown_stale_after = payload.get("crown_stale_after_seconds")
        if crown_stale_after is not None:
            self.crown_stale_after_seconds = self._as_float(
                crown_stale_after,
                fallback=self.crown_stale_after_seconds,
                minimum=0.5,
            )

        crown_subscribe_now = bool(payload.get("crown_subscribe_now", False))

        mode_request = payload.get("mode")
        if mode_request is None and payload.get("action") == "set_mode":
            mode_request = payload.get("value")
        normalized_mode_request = str(mode_request).strip().lower() if mode_request is not None else ""
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

        cubs_team_id = payload.get("cubs_team_id")
        if cubs_team_id is None and normalized_mode_request == "cubs":
            cubs_team_id = payload.get("team_id")
        if cubs_team_id is not None:
            self.cubs_team_id = self._as_int(cubs_team_id, fallback=self.cubs_team_id, minimum=1)
            self.cubs_next_fetch_time = 0.0

        cubs_refresh = payload.get("cubs_refresh_seconds")
        if cubs_refresh is not None:
            self.cubs_refresh_seconds = self._as_int(cubs_refresh, fallback=self.cubs_refresh_seconds, minimum=5)

        if bool(payload.get("cubs_refresh_now", False)):
            self._refresh_cubs_if_due(now=time.time(), force=True)

        if crown_subscribe_now:
            self._maybe_send_crown_subscribe(now=time.time(), force=True)

        if bool(payload.get("crown_reset", False)):
            self.crown_meter_state = {
                "levels": [],
                "updated_at": 0.0,
                "status_text": self.crown_waiting_text,
            }
            self.crown_last_payload_time = 0.0
            self.crown_last_render_key = ""
            self.crown_last_frame_time = 0.0
            self.crown_last_subscribe_time = 0.0

    def _close_crown_udp_socket(self) -> None:
        """Close UDP socket used by Crown mode."""
        if self.crown_udp_socket is None:
            return

        try:
            self.crown_udp_socket.close()
        except OSError:
            pass
        finally:
            self.crown_udp_socket = None
            self.crown_udp_bound_address = ""

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

        if normalized_mode == "cubs":
            self.cubs_last_render_key = ""
            self._refresh_cubs_if_due(now=time.time(), force=True)

        if normalized_mode == "crown":
            self.crown_last_render_key = ""
            self.crown_last_frame_time = 0.0
            self.crown_last_subscribe_time = 0.0
        else:
            self._close_crown_tcp_socket()

        logger.info(
            f"Mode changed from '{previous_mode}' to '{normalized_mode}'"
            + (f" ({reason})" if reason else "")
        )

    def _normalize_mode(self, requested_mode: str) -> str:
        """Normalize unknown or disabled modes back to scoreboard mode."""
        normalized = str(requested_mode).strip().lower()
        if normalized == "crown" and not self.crown_enabled:
            logger.warning("Mode 'crown' requested but crown.enabled is false. Using 'scoreboard'.")
            return "scoreboard"

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

        self._close_crown_udp_socket()
        self._close_crown_tcp_socket()

        if self.scoreboard:
            self.scoreboard.shutdown()

        logger.info("Scoreboard application stopped")


def main():
    """Main entry point"""
    app = ScoreboardApp()
    app.start()


if __name__ == "__main__":
    main()
