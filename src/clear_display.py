"""Utility to explicitly clear the LED matrix display."""

import argparse
import logging
import time
from typing import Any, Dict

import yaml

from src.scoreboard import LEDScoreboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> Dict[str, Any]:
    """Load matrix configuration from YAML file."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return config.get("matrix", {})


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear LED matrix display")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to scoreboard config file",
    )
    args = parser.parse_args()

    try:
        matrix_config = load_config(args.config)
        display = LEDScoreboard(**matrix_config)

        # Clear more than once to ensure latched pixels are removed.
        display.clear()
        time.sleep(0.05)
        display.clear()
        display.shutdown()
        logger.info("Display clear command completed")
    except Exception as exc:
        logger.error(f"Display clear command failed: {exc}", exc_info=True)


if __name__ == "__main__":
    main()
