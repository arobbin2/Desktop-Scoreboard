"""Main scoreboard controller for LED matrix display"""

import logging
from typing import Optional, Dict, Any
from PIL import Image, ImageDraw, ImageFont

try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
except ImportError:
    # For development without hardware
    RGBMatrix = None
    RGBMatrixOptions = None

logger = logging.getLogger(__name__)


class LEDScoreboard:
    """Controls LED matrix display"""

    def __init__(
        self,
        width: int = 64,
        height: int = 32,
        brightness: int = 90,
        gpio_slowdown: int = 10,
        hardware_mapping: str = "regular",
        chain_length: int = 4,
        parallel: int = 1,
    ):
        """
        Initialize LED matrix scoreboard

        Args:
            width: Matrix width in pixels
            height: Matrix height in pixels
            brightness: Brightness level (0-100)
            gpio_slowdown: GPIO slowdown factor
            hardware_mapping: GPIO mapping type (adafruit-hat, regular, etc.)
            chain_length: Number of chained matrices horizontally
            parallel: Number of parallel matrices
        """
        self.width = width * chain_length
        self.height = height * parallel
        self.brightness = brightness
        self.matrix = None

        if RGBMatrix is not None:
            try:
                options = RGBMatrixOptions()
                options.rows = height
                options.cols = width
                options.brightness = brightness
                options.gpio_slowdown = gpio_slowdown
                options.hardware_mapping = hardware_mapping
                options.chain_length = chain_length
                options.parallel = parallel
                options.daemon = True  # Allow daemon mode

                self.matrix = RGBMatrix(options=options)
                logger.info(f"LED Matrix initialized: {self.width}x{self.height}")
            except Exception as e:
                logger.error(f"Failed to initialize LED matrix: {e}")
                logger.warning("Operating in mock mode (no actual hardware)")
        else:
            logger.warning("rpi-rgb-led-matrix not available. Operating in mock mode.")

        # Current display state
        self.current_text = ""
        self.current_data: Dict[str, Any] = {}

    def display_text(self, text: str, color: tuple = (255, 0, 0)) -> None:
        """
        Display text on the LED matrix

        Args:
            text: Text to display
            color: RGB color tuple (default: red)
        """
        self.current_text = text
        self._render_text(text, color)

    def display_data(self, data: Dict[str, Any]) -> None:
        """
        Display structured scoreboard data

        Args:
            data: Dictionary containing scoreboard data
                Expected keys: team1, team2, score1, score2, status, etc.
        """
        self.current_data = data
        self._render_data(data)

    def _render_text(self, text: str, color: tuple = (255, 0, 0)) -> None:
        """Render text to the matrix"""
        if self.matrix is None:
            logger.info(f"Mock display text: {text} (color: {color})")
            return

        try:
            # Create image for rendering
            image = Image.new("RGB", (self.width, self.height), color=(0, 0, 0))
            draw = ImageDraw.Draw(image)

            # Try to use a nice font, fall back to default
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
            except OSError:
                font = ImageFont.load_default()

            # Calculate text position (centered)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (self.width - text_width) // 2
            y = (self.height - text_height) // 2

            draw.text((x, y), text, fill=color, font=font)

            # Display on matrix
            self.matrix.SetImage(image)
            logger.debug(f"Rendered text: {text}")
        except Exception as e:
            logger.error(f"Error rendering text: {e}")

    def _render_data(self, data: Dict[str, Any]) -> None:
        """Render structured scoreboard data"""
        if self.matrix is None:
            logger.info(f"Mock display data: {data}")
            return

        try:
            image = Image.new("RGB", (self.width, self.height), color=(0, 0, 0))
            draw = ImageDraw.Draw(image)

            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 12)
                small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 8)
            except OSError:
                font = ImageFont.load_default()
                small_font = ImageFont.load_default()

            # Example layout: team1 score1 vs team2 score2 with clock
            score1 = data.get("score1", "0")
            score2 = data.get("score2", "0")
            team1 = data.get("team1", "T1")[:3]  # Truncate to 3 chars
            team2 = data.get("team2", "T2")[:3]
            clock = data.get("clock", "")

            # Draw clock in top right if provided
            if clock:
                draw.text((self.width - 20, 2), str(clock), fill=(100, 100, 255), font=small_font)

            # Draw teams and scores
            draw.text((2, 2), team1, fill=(255, 0, 0), font=small_font)
            draw.text((22, 2), str(score1), fill=(0, 255, 0), font=font)
            draw.text((40, 2), "vs", fill=(255, 255, 0), font=small_font)
            draw.text((50, 2), str(score2), fill=(0, 255, 0), font=font)
            draw.text((2, 20), team2, fill=(255, 0, 0), font=small_font)

            # Draw status if provided
            status = data.get("status", "")
            if status:
                draw.text((2, 28), status, fill=(255, 255, 255), font=small_font)

            self.matrix.SetImage(image)
            logger.debug(f"Rendered data: {data}")
        except Exception as e:
            logger.error(f"Error rendering data: {e}")

    def clear(self) -> None:
        """Clear the display"""
        if self.matrix is None:
            logger.debug("Mock clear display")
            return

        try:
            image = Image.new("RGB", (self.width, self.height), color=(0, 0, 0))
            self.matrix.SetImage(image)
            logger.debug("Display cleared")
        except Exception as e:
            logger.error(f"Error clearing display: {e}")

    def set_brightness(self, brightness: int) -> None:
        """
        Set matrix brightness

        Args:
            brightness: Brightness level (0-100)
        """
        if brightness < 0 or brightness > 100:
            logger.warning(f"Invalid brightness value: {brightness}. Expected 0-100")
            return

        self.brightness = brightness
        if self.matrix is not None:
            try:
                self.matrix.brightness = brightness
                logger.info(f"Brightness set to {brightness}")
            except Exception as e:
                logger.error(f"Error setting brightness: {e}")

    def shutdown(self) -> None:
        """Clean shutdown of the matrix"""
        self.clear()
        if self.matrix is not None:
            try:
                del self.matrix
                logger.info("Matrix shutdown complete")
            except Exception as e:
                logger.error(f"Error during matrix shutdown: {e}")
