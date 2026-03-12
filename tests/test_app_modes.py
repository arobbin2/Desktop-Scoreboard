"""Tests for app mode switching and RSS parsing."""

import sys
import time
import types
import unittest
from unittest.mock import patch, MagicMock

# Stub paho-mqtt for environments where dependency is not installed.
if "paho" not in sys.modules:
    paho_module = types.ModuleType("paho")
    mqtt_module = types.ModuleType("paho.mqtt")
    mqtt_client_module = types.ModuleType("paho.mqtt.client")

    class _DummyClient:
        CallbackAPIVersion = types.SimpleNamespace(VERSION1=1)

        def __init__(self, *args, **kwargs):
            self.on_connect = None
            self.on_disconnect = None
            self.on_message = None

        def username_pw_set(self, *args, **kwargs):
            return None

        def connect(self, *args, **kwargs):
            return None

        def loop_start(self):
            return None

        def loop_stop(self):
            return None

        def disconnect(self):
            return None

        def subscribe(self, *args, **kwargs):
            return None

    mqtt_client_module.Client = _DummyClient
    mqtt_client_module.CallbackAPIVersion = _DummyClient.CallbackAPIVersion
    mqtt_module.client = mqtt_client_module
    paho_module.mqtt = mqtt_module

    sys.modules["paho"] = paho_module
    sys.modules["paho.mqtt"] = mqtt_module
    sys.modules["paho.mqtt.client"] = mqtt_client_module

from src.app import ScoreboardApp


class _FakeHTTPResponse:
    """Simple context manager to mimic urllib response objects in tests."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestAppModes(unittest.TestCase):
    """Validate mode controls and RSS helpers."""

    def setUp(self):
        self.app = ScoreboardApp(config_path="config/does-not-exist.yaml")

    def tearDown(self):
        self.app._close_crown_udp_socket()

    def test_normalize_mode_falls_back(self):
        self.assertEqual(self.app._normalize_mode("rss"), "rss")
        self.assertEqual(self.app._normalize_mode("cubs"), "cubs")
        self.assertEqual(self.app._normalize_mode("crown"), "scoreboard")
        self.assertEqual(self.app._normalize_mode("not-a-mode"), "scoreboard")

    def test_crown_mode_requires_enable_flag(self):
        self.app.active_mode = "scoreboard"
        self.app._handle_control_message("mode:crown")
        self.assertEqual(self.app.active_mode, "scoreboard")

    def test_control_dict_can_enable_and_switch_to_crown_mode(self):
        payload = {
            "crown_enabled": True,
            "mode": "crown",
        }

        self.app._handle_control_message(payload)

        self.assertTrue(self.app.crown_enabled)
        self.assertEqual(self.app.active_mode, "crown")

    def test_crown_meter_payload_updates_state(self):
        payload = {
            "crown_enabled": True,
            "mode": "crown",
            "crown_meter_topic": "scoreboard/crown/meter",
        }
        self.app._handle_control_message(payload)

        self.app._on_mqtt_message(
            "scoreboard/crown/meter",
            {"channels": [{"db": -13.2}, {"db": -9.8}]},
        )

        self.assertEqual(self.app.crown_meter_state["levels"], [-13.2, -9.8])
        self.assertGreater(self.app.crown_last_payload_time, 0.0)

    def test_crown_mode_falls_back_when_feed_stale(self):
        self.app._handle_control_message({"crown_enabled": True, "mode": "crown"})
        self.app.crown_stale_after_seconds = 1.0
        self.app.crown_last_payload_time = time.time() - 10.0
        self.app.scoreboard = MagicMock()

        self.app._tick_active_mode()

        self.assertEqual(self.app.active_mode, "scoreboard")

    def test_extract_hex_bytes_from_udp_ascii_formats(self):
        self.assertEqual(self.app._extract_hex_bytes_from_udp_payload(b"7F"), [0x7F])
        self.assertEqual(self.app._extract_hex_bytes_from_udp_payload(b"0x7F,1A"), [0x7F, 0x1A])
        self.assertEqual(self.app._extract_hex_bytes_from_udp_payload(b"7F 1A"), [0x7F, 0x1A])

    def test_extract_udp_meter_level_from_hex_byte_index(self):
        self.app.crown_meter_min_db = -60.0
        self.app.crown_meter_max_db = 0.0
        self.app.crown_udp_hex_byte_index = 0

        low = self.app._extract_udp_meter_level(b"00")
        high = self.app._extract_udp_meter_level(b"FF")

        self.assertEqual(low, -60.0)
        self.assertEqual(high, 0.0)

    def test_extract_udp_meter_level_uses_selected_byte(self):
        self.app.crown_meter_min_db = -60.0
        self.app.crown_meter_max_db = 0.0
        self.app.crown_udp_hex_byte_index = 1

        level = self.app._extract_udp_meter_level(b"10 80")

        self.assertIsNotNone(level)
        self.assertGreater(level, -40.0)
        self.assertLess(level, -20.0)

    def test_control_string_switches_mode(self):
        self.app.active_mode = "scoreboard"
        with patch.object(self.app, "_refresh_rss_if_due"):
            self.app._handle_control_message("mode:rss")
        self.assertEqual(self.app.active_mode, "rss")

    def test_control_dict_updates_rss_settings(self):
        payload = {
            "mode": "rss",
            "feed_url": "https://example.com/rss.xml",
            "refresh_seconds": 180,
            "rss_refresh_now": True,
        }

        with patch.object(self.app, "_refresh_rss_if_due") as refresh_mock:
            self.app._handle_control_message(payload)

        self.assertEqual(self.app.active_mode, "rss")
        self.assertEqual(self.app.rss_feed_url, "https://example.com/rss.xml")
        self.assertEqual(self.app.rss_refresh_seconds, 180)
        refresh_mock.assert_called()

    def test_fetch_rss_headlines_parses_rss(self):
        rss_xml = b"""
        <rss>
          <channel>
            <item><title>Headline One</title></item>
            <item><title>Headline Two</title></item>
          </channel>
        </rss>
        """

        self.app.rss_feed_url = "https://example.com/rss.xml"
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(rss_xml)):
            headlines = self.app._fetch_rss_headlines()

        self.assertEqual(headlines, ["Headline One", "Headline Two"])

    def test_fetch_rss_headlines_parses_atom(self):
        atom_xml = b"""
        <feed xmlns=\"http://www.w3.org/2005/Atom\">
          <entry><title>Atom One</title></entry>
          <entry><title>Atom Two</title></entry>
        </feed>
        """

        self.app.rss_feed_url = "https://example.com/atom.xml"
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(atom_xml)):
            headlines = self.app._fetch_rss_headlines()

        self.assertEqual(headlines, ["Atom One", "Atom Two"])

    def test_control_dict_updates_cubs_settings(self):
        payload = {
            "mode": "cubs",
            "team_id": 119,
            "cubs_refresh_seconds": 12,
            "cubs_refresh_now": True,
        }

        with patch.object(self.app, "_refresh_cubs_if_due") as refresh_mock:
            self.app._handle_control_message(payload)

        self.assertEqual(self.app.active_mode, "cubs")
        self.assertEqual(self.app.cubs_team_id, 119)
        self.assertEqual(self.app.cubs_refresh_seconds, 12)
        refresh_mock.assert_called()

    def test_build_cubs_display_state_includes_counts_and_bases(self):
        payload = {
            "gameData": {
                "status": {"detailedState": "In Progress"},
                "teams": {
                    "away": {"abbreviation": "CHC"},
                    "home": {"abbreviation": "MIL"},
                },
            },
            "liveData": {
                "linescore": {
                    "inningState": "Top",
                    "currentInning": 7,
                    "balls": 2,
                    "strikes": 1,
                    "outs": 1,
                    "teams": {
                        "away": {"runs": 4},
                        "home": {"runs": 3},
                    },
                    "offense": {
                        "first": {"id": 1},
                        "second": {"id": 2},
                    },
                }
            },
        }

        built = self.app._build_cubs_display_state(payload)
        self.assertEqual(built["away_team"], "CHC")
        self.assertEqual(built["home_team"], "MIL")
        self.assertEqual(built["away_score"], "4")
        self.assertEqual(built["home_score"], "3")
        self.assertEqual(built["inning_text"], "TOP 7")
        self.assertEqual(built["count_text"], "B2 S1 O1")
        self.assertEqual(built["bases_text"], "BASES: 1B 2B")


if __name__ == "__main__":
    unittest.main()
