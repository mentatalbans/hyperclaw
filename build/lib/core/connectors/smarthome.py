"""
Smart Home & IoT Connectors.
Integration with Philips Hue, Home Assistant, Sonos, Spotify, MQTT, SmartThings.
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Device:
    """Smart home device."""
    id: str
    name: str
    type: str
    state: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class SmartHomeConnector(ABC):
    """Abstract base for smart home connectors."""

    name: str = "base"

    @abstractmethod
    async def get_devices(self) -> list[Device]:
        """Get all devices."""
        pass

    @abstractmethod
    async def control(self, device_id: str, command: str, **kwargs) -> dict:
        """Control a device."""
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# PHILIPS HUE
# ═══════════════════════════════════════════════════════════════════════════════

class PhilipsHueConnector(SmartHomeConnector):
    """Philips Hue smart lighting connector."""

    name = "hue"

    def __init__(
        self,
        bridge_ip: str | None = None,
        username: str | None = None,
        api_key: str | None = None,
    ):
        self.bridge_ip = bridge_ip or os.environ.get("HUE_BRIDGE_IP")
        self.username = username or api_key or os.environ.get("HUE_USERNAME")
        self.base_url = f"http://{self.bridge_ip}/api/{self.username}"

    async def get_devices(self) -> list[Device]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/lights", timeout=10.0)
            response.raise_for_status()
            data = response.json()

            return [
                Device(
                    id=light_id,
                    name=light["name"],
                    type="light",
                    state={
                        "on": light["state"]["on"],
                        "brightness": light["state"].get("bri", 0),
                        "hue": light["state"].get("hue"),
                        "saturation": light["state"].get("sat"),
                    },
                    raw=light,
                )
                for light_id, light in data.items()
            ]

    async def control(self, device_id: str, command: str, **kwargs) -> dict:
        state: dict[str, Any] = {}

        if command == "on":
            state["on"] = True
        elif command == "off":
            state["on"] = False
        elif command == "brightness":
            state["bri"] = int(kwargs.get("value", 254))
        elif command == "color":
            state["hue"] = int(kwargs.get("hue", 0))
            state["sat"] = int(kwargs.get("saturation", 254))
        elif command == "scene":
            # Activate a scene
            scene_id = kwargs.get("scene_id")
            async with httpx.AsyncClient() as client:
                response = await client.put(
                    f"{self.base_url}/groups/0/action",
                    json={"scene": scene_id},
                    timeout=10.0,
                )
                response.raise_for_status()
                return response.json()

        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{self.base_url}/lights/{device_id}/state",
                json=state,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    async def get_scenes(self) -> list[dict]:
        """Get all scenes."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/scenes", timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return [{"id": sid, **scene} for sid, scene in data.items()]


# ═══════════════════════════════════════════════════════════════════════════════
# HOME ASSISTANT
# ═══════════════════════════════════════════════════════════════════════════════

class HomeAssistantConnector(SmartHomeConnector):
    """Home Assistant connector."""

    name = "homeassistant"

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
    ):
        self.url = (url or os.environ.get("HOMEASSISTANT_URL", "http://localhost:8123")).rstrip("/")
        self.token = token or os.environ.get("HOMEASSISTANT_TOKEN")

    async def get_devices(self) -> list[Device]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.url}/api/states",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            states = response.json()

            return [
                Device(
                    id=entity["entity_id"],
                    name=entity["attributes"].get("friendly_name", entity["entity_id"]),
                    type=entity["entity_id"].split(".")[0],
                    state={"state": entity["state"], **entity["attributes"]},
                    raw=entity,
                )
                for entity in states
            ]

    async def control(self, device_id: str, command: str, **kwargs) -> dict:
        domain = device_id.split(".")[0]

        service_data = {"entity_id": device_id, **kwargs}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.url}/api/services/{domain}/{command}",
                headers={"Authorization": f"Bearer {self.token}"},
                json=service_data,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def call_service(self, domain: str, service: str, **kwargs) -> dict:
        """Call any Home Assistant service."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.url}/api/services/{domain}/{service}",
                headers={"Authorization": f"Bearer {self.token}"},
                json=kwargs,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# SONOS
# ═══════════════════════════════════════════════════════════════════════════════

class SonosConnector(SmartHomeConnector):
    """Sonos speaker connector (via local API or Sonos Cloud)."""

    name = "sonos"

    def __init__(
        self,
        api_key: str | None = None,
        household_id: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("SONOS_API_KEY")
        self.household_id = household_id or os.environ.get("SONOS_HOUSEHOLD_ID")
        self.base_url = "https://api.ws.sonos.com/control/api/v1"

    async def get_devices(self) -> list[Device]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/households/{self.household_id}/groups",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            devices = []
            for group in data.get("groups", []):
                for player in group.get("players", []):
                    devices.append(
                        Device(
                            id=player["id"],
                            name=player["name"],
                            type="speaker",
                            state={"zone": group["name"]},
                            raw=player,
                        )
                    )
            return devices

    async def control(self, device_id: str, command: str, **kwargs) -> dict:
        async with httpx.AsyncClient() as client:
            if command == "play":
                endpoint = f"{self.base_url}/groups/{device_id}/playback/play"
            elif command == "pause":
                endpoint = f"{self.base_url}/groups/{device_id}/playback/pause"
            elif command == "volume":
                endpoint = f"{self.base_url}/players/{device_id}/playerVolume"
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"volume": kwargs.get("value", 50)},
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()
            else:
                raise ValueError(f"Unknown command: {command}")

            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# SPOTIFY
# ═══════════════════════════════════════════════════════════════════════════════

class SpotifyConnector(SmartHomeConnector):
    """Spotify playback connector."""

    name = "spotify"

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token or os.environ.get("SPOTIFY_ACCESS_TOKEN")
        self.base_url = "https://api.spotify.com/v1"

    async def get_devices(self) -> list[Device]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/me/player/devices",
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                Device(
                    id=device["id"],
                    name=device["name"],
                    type=device["type"],
                    state={
                        "active": device["is_active"],
                        "volume": device["volume_percent"],
                    },
                    raw=device,
                )
                for device in data.get("devices", [])
            ]

    async def control(self, device_id: str, command: str, **kwargs) -> dict:
        async with httpx.AsyncClient() as client:
            if command == "play":
                uri = kwargs.get("uri")
                body = {"device_id": device_id}
                if uri:
                    if "track" in uri:
                        body["uris"] = [uri]
                    else:
                        body["context_uri"] = uri

                response = await client.put(
                    f"{self.base_url}/me/player/play",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params={"device_id": device_id},
                    json=body if uri else None,
                    timeout=30.0,
                )
            elif command == "pause":
                response = await client.put(
                    f"{self.base_url}/me/player/pause",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params={"device_id": device_id},
                    timeout=30.0,
                )
            elif command == "next":
                response = await client.post(
                    f"{self.base_url}/me/player/next",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params={"device_id": device_id},
                    timeout=30.0,
                )
            elif command == "previous":
                response = await client.post(
                    f"{self.base_url}/me/player/previous",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params={"device_id": device_id},
                    timeout=30.0,
                )
            elif command == "volume":
                response = await client.put(
                    f"{self.base_url}/me/player/volume",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params={
                        "device_id": device_id,
                        "volume_percent": kwargs.get("value", 50),
                    },
                    timeout=30.0,
                )
            else:
                raise ValueError(f"Unknown command: {command}")

            if response.status_code == 204:
                return {"status": "ok"}
            response.raise_for_status()
            return response.json() if response.content else {"status": "ok"}

    async def search(self, query: str, types: str = "track", limit: int = 10) -> dict:
        """Search Spotify."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/search",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"q": query, "type": types, "limit": limit},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# MQTT
# ═══════════════════════════════════════════════════════════════════════════════

class MQTTConnector(SmartHomeConnector):
    """MQTT IoT protocol connector."""

    name = "mqtt"

    def __init__(
        self,
        broker: str | None = None,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
    ):
        self.broker = broker or os.environ.get("MQTT_BROKER", "localhost")
        self.port = int(os.environ.get("MQTT_PORT", port))
        self.username = username or os.environ.get("MQTT_USERNAME")
        self.password = password or os.environ.get("MQTT_PASSWORD")
        self._client = None

    def _get_client(self):
        """Get or create MQTT client."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise ImportError("paho-mqtt required: pip install paho-mqtt")

        if self._client is None:
            self._client = mqtt.Client()
            if self.username:
                self._client.username_pw_set(self.username, self.password)
            self._client.connect(self.broker, self.port)
            self._client.loop_start()

        return self._client

    async def get_devices(self) -> list[Device]:
        # MQTT doesn't have a standard device discovery
        # Return empty list - devices are discovered via subscription
        return []

    async def control(self, device_id: str, command: str, **kwargs) -> dict:
        """Publish to MQTT topic."""
        import json

        client = self._get_client()
        topic = device_id
        payload = json.dumps({"command": command, **kwargs})

        result = client.publish(topic, payload)
        result.wait_for_publish()

        return {"topic": topic, "payload": payload, "published": result.is_published()}

    async def subscribe(self, topic: str, callback) -> None:
        """Subscribe to MQTT topic."""
        client = self._get_client()
        client.subscribe(topic)
        client.message_callback_add(topic, callback)

    async def publish(self, topic: str, payload: Any) -> dict:
        """Publish to MQTT topic."""
        import json

        client = self._get_client()
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)

        result = client.publish(topic, payload)
        result.wait_for_publish()

        return {"topic": topic, "published": result.is_published()}


# ═══════════════════════════════════════════════════════════════════════════════
# SAMSUNG SMARTTHINGS
# ═══════════════════════════════════════════════════════════════════════════════

class SmartThingsConnector(SmartHomeConnector):
    """Samsung SmartThings connector."""

    name = "smartthings"

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("SMARTTHINGS_TOKEN")
        self.base_url = "https://api.smartthings.com/v1"

    async def get_devices(self) -> list[Device]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/devices",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            return [
                Device(
                    id=device["deviceId"],
                    name=device["label"] or device["name"],
                    type=device["deviceTypeName"],
                    state={},
                    raw=device,
                )
                for device in data.get("items", [])
            ]

    async def control(self, device_id: str, command: str, **kwargs) -> dict:
        component = kwargs.get("component", "main")
        capability = kwargs.get("capability", "switch")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/devices/{device_id}/commands",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "commands": [
                        {
                            "component": component,
                            "capability": capability,
                            "command": command,
                            "arguments": kwargs.get("arguments", []),
                        }
                    ]
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()


# ═══════════════════════════════════════════════════════════════════════════════
# PROVIDER FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

SMARTHOME_CONNECTORS: dict[str, type[SmartHomeConnector]] = {
    "hue": PhilipsHueConnector,
    "philips_hue": PhilipsHueConnector,
    "homeassistant": HomeAssistantConnector,
    "hass": HomeAssistantConnector,
    "sonos": SonosConnector,
    "spotify": SpotifyConnector,
    "mqtt": MQTTConnector,
    "smartthings": SmartThingsConnector,
}


def get_smarthome_connector(name: str, **kwargs) -> SmartHomeConnector:
    """Get a smart home connector by name."""
    name = name.lower()

    if name not in SMARTHOME_CONNECTORS:
        available = ", ".join(sorted(SMARTHOME_CONNECTORS.keys()))
        raise ValueError(f"Unknown smart home connector: {name}. Available: {available}")

    return SMARTHOME_CONNECTORS[name](**kwargs)
