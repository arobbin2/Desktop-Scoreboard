"""Main scoreboard controller for LED matrix display"""

import logging
import re
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
        led_rgb_sequence: str = "RGB",
        title_text: str = "COMPTON",
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
            title_text: Header text shown above the center clock in data mode
        """
        self.width = width * chain_length
        self.height = height * parallel
        self.brightness = brightness
        resolved_title = str(title_text).strip()
        self.title_text = resolved_title if resolved_title else "COMPTON"
        self.matrix = None
        sequence = str(led_rgb_sequence).upper()
        if sorted(sequence) != ["B", "G", "R"]:
            logger.warning(
                f"Invalid led_rgb_sequence '{led_rgb_sequence}'. Falling back to 'RGB'."
            )
            sequence = "RGB"

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
                options.led_rgb_sequence = sequence
                # Keep process attached for service managers like systemd.
                options.daemon = False

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

    def display_clock(
        self,
        text: str,
        color: tuple = (255, 255, 0),
        font_size: Optional[int] = None,
        right_text: Optional[str] = None,
        right_text_color: tuple = (0, 102, 255),
    ) -> None:
        """Display a clock string with a larger font for readability."""
        self.current_text = text
        self._render_clock(text, color, font_size, right_text, right_text_color)

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

            def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
                path = (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                    if bold
                    else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                )
                try:
                    return ImageFont.truetype(path, max(8, int(size)))
                except OSError:
                    return ImageFont.load_default()

            def fit_font(text: str, max_width: int, max_size: int, min_size: int = 8, bold: bool = False) -> ImageFont.ImageFont:
                for size in range(max_size, min_size - 1, -1):
                    candidate = load_font(size, bold=bold)
                    bbox = draw.textbbox((0, 0), text, font=candidate)
                    if (bbox[2] - bbox[0]) <= max_width:
                        return candidate
                return load_font(min_size, bold=bold)

            def draw_centered(cx: int, top: int, text: str, font: ImageFont.ImageFont, fill: tuple) -> None:
                bbox = draw.textbbox((0, 0), text, font=font)
                x = cx - ((bbox[2] - bbox[0]) // 2) - bbox[0]
                y = top - bbox[1]
                draw.text((x, y), text, font=font, fill=fill)

            team1 = str(data.get("team1", "HOME")).upper()[:4]
            team2 = str(data.get("team2", "AWAY")).upper()[:4]
            score1 = str(data.get("score1", "0"))[:2]
            score2 = str(data.get("score2", "0"))[:2]
            clock = str(data.get("clock") or data.get("time") or "--:--")
            status = str(data.get("status") or data.get("period") or "")
            title = self.title_text.upper()[:12]

            raw_period = (
                data.get("period_number")
                or data.get("period_num")
                or data.get("period")
                or data.get("quarter")
                or data.get("qtr")
                or ""
            )
            period_value = ""
            if isinstance(raw_period, (int, float)):
                period_value = str(int(raw_period))
            else:
                period_text = str(raw_period).strip()
                if period_text:
                    digit_match = re.search(r"\d+", period_text)
                    period_value = digit_match.group(0) if digit_match else period_text.upper()[:2]

            if not period_value and status:
                status_match = re.search(r"\d+", status)
                if status_match:
                    period_value = status_match.group(0)

            if not period_value:
                period_value = "-"

            primary = (255, 255, 255)
            secondary = (255, 255, 0)
            period_label_color = (255, 0, 0)

            side_center_left = int(self.width * 0.11)
            side_center_right = int(self.width * 0.89)
            period_center_left = int(self.width * 0.28)
            center_x = self.width // 2

            team_font = fit_font(team1 if len(team1) >= len(team2) else team2, int(self.width * 0.24), max(11, int(self.height * 0.42)), min_size=9, bold=True)
            score_font = fit_font("88", int(self.width * 0.16), max(14, int(self.height * 0.62)), min_size=12, bold=True)
            clock_font = fit_font(clock, int(self.width * 0.52), max(16, int(self.height * 0.8)), min_size=12, bold=True)
            period_label_font = fit_font("PER", int(self.width * 0.12), max(9, int(self.height * 0.3)), min_size=8, bold=True)
            period_value_font = fit_font(period_value, int(self.width * 0.12), max(12, int(self.height * 0.56)), min_size=10, bold=True)
            title_font = fit_font(title, int(self.width * 0.34), max(10, int(self.height * 0.3)), min_size=8, bold=True)

            team_top = 1
            score_top = int(self.height * 0.42)
            title_bbox = draw.textbbox((0, 0), title, font=title_font)
            title_height = title_bbox[3] - title_bbox[1]
            title_top = 1
            title_bottom = title_top + title_height
            clock_bbox = draw.textbbox((0, 0), clock, font=clock_font)
            clock_height = clock_bbox[3] - clock_bbox[1]
            centered_clock_top = int((self.height - clock_height) / 2) + 2
            clock_top = max(title_bottom + 2, centered_clock_top)

            draw_centered(side_center_left, team_top, team1, team_font, secondary)
            draw_centered(side_center_right, team_top, team2, team_font, secondary)
            draw_centered(side_center_left, score_top, score1, score_font, primary)
            draw_centered(side_center_right, score_top, score2, score_font, primary)
            draw_centered(period_center_left, team_top, "PER", period_label_font, period_label_color)
            draw_centered(period_center_left, score_top, period_value, period_value_font, primary)
            draw_centered(center_x, title_top, title, title_font, secondary)
            draw_centered(center_x, clock_top, clock, clock_font, primary)

            self.matrix.SetImage(image)
            logger.debug(f"Rendered data: {data}")
        except Exception as e:
            logger.error(f"Error rendering data: {e}")

    def _render_clock(
        self,
        text: str,
        color: tuple = (255, 255, 0),
        font_size: Optional[int] = None,
        right_text: Optional[str] = None,
        right_text_color: tuple = (0, 102, 255),
    ) -> None:
        """Render larger clock text centered on the matrix."""
        if self.matrix is None:
            if right_text:
                logger.info(
                    f"Mock display clock: {text} {right_text} "
                    f"(color: {color}, right_text_color: {right_text_color})"
                )
            else:
                logger.info(f"Mock display clock: {text} (color: {color})")
            return

        try:
            image = Image.new("RGB", (self.width, self.height), color=(0, 0, 0))
            draw = ImageDraw.Draw(image)

            resolved_font_size = font_size if font_size is not None else max(20, min(24, self.height))
            resolved_font_size = max(8, int(resolved_font_size))

            def load_font(size: int) -> ImageFont.ImageFont:
                try:
                    return ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                        max(8, int(size)),
                    )
                except OSError:
                    return ImageFont.load_default()

            clock_font = load_font(resolved_font_size)
            right_font_size = max(8, int(resolved_font_size * 0.52))
            right_font = load_font(right_font_size)

            clock_bbox = draw.textbbox((0, 0), text, font=clock_font)
            clock_width = clock_bbox[2] - clock_bbox[0]
            clock_height = clock_bbox[3] - clock_bbox[1]

            right_text_value = str(right_text).strip() if right_text else ""
            spacing = 3
            right_width = 0
            right_bbox = None
            if right_text_value:
                right_bbox = draw.textbbox((0, 0), right_text_value, font=right_font)
                right_width = right_bbox[2] - right_bbox[0]

            total_width = clock_width + (spacing + right_width if right_width > 0 else 0)
            max_width = self.width - 2
            while total_width > max_width and resolved_font_size > 8:
                resolved_font_size -= 1
                clock_font = load_font(resolved_font_size)
                right_font = load_font(max(8, int(resolved_font_size * 0.52)))

                clock_bbox = draw.textbbox((0, 0), text, font=clock_font)
                clock_width = clock_bbox[2] - clock_bbox[0]
                clock_height = clock_bbox[3] - clock_bbox[1]

                if right_text_value:
                    right_bbox = draw.textbbox((0, 0), right_text_value, font=right_font)
                    right_width = right_bbox[2] - right_bbox[0]
                else:
                    right_width = 0
                    right_bbox = None

                total_width = clock_width + (spacing + right_width if right_width > 0 else 0)

            clock_x = ((self.width - total_width) // 2) - clock_bbox[0]
            clock_y = ((self.height - clock_height) // 2) - clock_bbox[1]

            draw.text((clock_x, clock_y), text, fill=color, font=clock_font)

            if right_text_value and right_bbox is not None:
                right_height = right_bbox[3] - right_bbox[1]
                right_x = clock_x + clock_width + spacing - right_bbox[0]
                right_y = ((self.height - right_height) // 2) - right_bbox[1]
                draw.text((right_x, right_y), right_text_value, fill=right_text_color, font=right_font)

            self.matrix.SetImage(image)
            if right_text_value:
                logger.debug(f"Rendered clock: {text} {right_text_value}")
            else:
                logger.debug(f"Rendered clock: {text}")
        except Exception as e:
            logger.error(f"Error rendering clock: {e}")

    def clear(self) -> None:
        """Clear the display"""
        if self.matrix is None:
            logger.debug("Mock clear display")
            return

        try:
            # Prefer the library-native clear call for reliable panel blanking.
            self.matrix.Clear()

            # Keep an explicit black frame as a fallback for adapters that
            # require an image push to fully clear latched pixels.
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
