"""MQTT client for receiving scoreboard data from Node-RED"""

import json
import logging
from typing import Callable, Optional
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class ScoreboardMQTTClient:
    """MQTT client for receiving scoreboard updates"""

    def __init__(
        self,
        broker_host: str,
        broker_port: int = 1883,
        client_id: str = "scoreboard",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Initialize MQTT client

        Args:
            broker_host: MQTT broker hostname/IP
            broker_port: MQTT broker port (default 1883)
            client_id: Client ID for MQTT connection
            username: Optional username for authentication
            password: Optional password for authentication
        """
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client_id = client_id
        self.username = username
        self.password = password
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        self.on_message_callback: Optional[Callable] = None

        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def set_message_callback(self, callback: Callable[[str, dict], None]) -> None:
        """
        Set callback for message reception

        Args:
            callback: Function(topic, payload_dict) called on message reception
        """
        self.on_message_callback = callback

    def connect(self) -> None:
        """Connect to MQTT broker"""
        if self.username and self.password:
            self.client.username_pw_set(self.username, self.password)

        logger.info(f"Connecting to MQTT broker at {self.broker_host}:{self.broker_port}")
        self.client.connect(self.broker_host, self.broker_port, keepalive=60)

    def start(self) -> None:
        """Start the MQTT client loop"""
        self.client.loop_start()
        logger.info("MQTT client started")

    def stop(self) -> None:
        """Stop the MQTT client loop"""
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT client stopped")

    def subscribe(self, topic: str) -> None:
        """
        Subscribe to MQTT topic

        Args:
            topic: Topic to subscribe to (supports wildcards)
        """
        self.client.subscribe(topic)
        logger.info(f"Subscribed to topic: {topic}")

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        if rc == 0:
            logger.info("Connected to MQTT broker")
        else:
            logger.error(f"Failed to connect to MQTT broker. Return code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback"""
        if rc != 0:
            logger.warning(f"Unexpected disconnection from MQTT broker. Return code: {rc}")
        else:
            logger.info("Disconnected from MQTT broker")

    def _on_message(self, client, userdata, msg):
        """MQTT message callback"""
        try:
            # Try to parse as JSON
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Fall back to string payload
            payload = msg.payload.decode("utf-8")
            logger.warning(f"Received non-JSON payload on {msg.topic}: {payload}")

        if self.on_message_callback:
            self.on_message_callback(msg.topic, payload)
        else:
            logger.debug(f"Received message on {msg.topic}: {payload}")
