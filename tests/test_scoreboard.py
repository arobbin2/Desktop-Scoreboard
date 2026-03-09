"""Unit tests for scoreboard functionality"""

import unittest
from src.scoreboard import LEDScoreboard


class TestLEDScoreboard(unittest.TestCase):
    """Test cases for LED scoreboard"""

    def setUp(self):
        """Set up test fixtures"""
        self.scoreboard = LEDScoreboard()

    def test_initialization(self):
        """Test scoreboard initialization"""
        self.assertEqual(self.scoreboard.width, 64)
        self.assertEqual(self.scoreboard.height, 32)
        self.assertEqual(self.scoreboard.brightness, 100)

    def test_display_text(self):
        """Test displaying text"""
        self.scoreboard.display_text("TEST", color=(255, 0, 0))
        self.assertEqual(self.scoreboard.current_text, "TEST")

    def test_display_data(self):
        """Test displaying structured data"""
        data = {
            "team1": "HOME",
            "team2": "AWAY",
            "score1": 10,
            "score2": 7,
            "status": "2nd Quarter",
        }
        self.scoreboard.display_data(data)
        self.assertEqual(self.scoreboard.current_data, data)

    def test_brightness_validation(self):
        """Test brightness value validation"""
        self.scoreboard.set_brightness(50)
        self.assertEqual(self.scoreboard.brightness, 50)

        # Invalid values should not change brightness
        self.scoreboard.set_brightness(-1)
        self.assertEqual(self.scoreboard.brightness, 50)

        self.scoreboard.set_brightness(101)
        self.assertEqual(self.scoreboard.brightness, 50)

    def test_clear(self):
        """Test clearing the display"""
        self.scoreboard.display_text("TEST")
        self.scoreboard.clear()
        self.assertEqual(self.scoreboard.current_text, "TEST")  # Text stays in memory

    def test_shutdown(self):
        """Test shutdown"""
        self.scoreboard.shutdown()  # Should not raise an error


if __name__ == "__main__":
    unittest.main()
