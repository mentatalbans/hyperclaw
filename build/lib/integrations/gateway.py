"""
HyperClawGateway — routes inbound messages from any platform to agents,
and routes agent outputs back to the originating platform.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from integrations.base import (
    BaseConnector,
    ConnectorCapability,
    ConnectorRegistry,
    InboundMessage,
    OutboundMessage,
)

if TYPE_CHECKING:
    from swarm.nexus import Nexus

logger = logging.getLogger("hyperclaw.gateway")


class HyperClawGateway:
    """
    Central gateway for routing messages between platforms and HyperClaw agents.

    - Polls messaging connectors for inbound messages
    - Routes inbound messages to Nexus for agent orchestration
    - Sends agent responses back to the originating platform
    - Supports broadcasting to multiple platforms
    """

    def __init__(
        self,
        registry: ConnectorRegistry,
        nexus: "Nexus | None" = None,
    ) -> None:
        self.registry = registry
        self.nexus = nexus
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._message_counts: dict[str, int] = {}
        self._active_platforms: set[str] = set()
        self._poll_interval: float = 1.0  # seconds

    @property
    def message_counts(self) -> dict[str, int]:
        """Message counts per platform."""
        return self._message_counts.copy()

    @property
    def active_platforms(self) -> set[str]:
        """Currently active platforms."""
        return self._active_platforms.copy()

    async def start(self) -> None:
        """Start polling all messaging connectors."""
        if self._running:
            logger.warning("Gateway already running")
            return

        self._running = True
        messaging_connectors = self.registry.get_messaging_connectors()

        for connector in messaging_connectors:
            if connector.supports(ConnectorCapability.RECEIVE_MESSAGE):
                task = asyncio.create_task(
                    self._poll_connector(connector),
                    name=f"gateway-poll-{connector.connector_id}",
                )
                self._tasks.append(task)
                self._active_platforms.add(connector.info.platform)
                logger.info(f"Started polling {connector.connector_id}")

        logger.info(
            f"Gateway started with {len(self._tasks)} connectors: "
            f"{', '.join(self._active_platforms)}"
        )

    async def stop(self) -> None:
        """Gracefully stop the gateway."""
        self._running = False

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks.clear()
        self._active_platforms.clear()
        logger.info("Gateway stopped")

    async def _poll_connector(self, connector: BaseConnector) -> None:
        """Poll a connector for inbound messages."""
        platform = connector.info.platform

        while self._running:
            try:
                async for message in connector.receive():
                    await self.handle_inbound(message)
                    self._message_counts[platform] = (
                        self._message_counts.get(platform, 0) + 1
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error polling {connector.connector_id}: {e}")

            await asyncio.sleep(self._poll_interval)

    async def handle_inbound(self, message: InboundMessage) -> None:
        """
        Route an inbound message to Nexus for processing.
        If Nexus is not available, just log the message.
        """
        logger.info(
            f"Received message from {message.platform}: "
            f"sender={message.sender_name}, content={message.content[:50]}..."
        )

        if self.nexus:
            try:
                # Nexus.orchestrate expects (task, domain, context)
                result = await self.nexus.orchestrate(
                    task=message.content,
                    domain="business",  # Default domain, could be inferred
                    context={
                        "source_platform": message.platform,
                        "sender_id": message.sender_id,
                        "sender_name": message.sender_name,
                        "thread_id": message.thread_id,
                        "channel_id": message.channel_id,
                        "message_id": message.message_id,
                    },
                )

                # Send response back to originating platform
                if result:
                    await self.reply(message, str(result))

            except Exception as e:
                logger.error(f"Error processing message: {e}")
        else:
            logger.debug("No Nexus configured, message logged only")

    async def reply(self, original: InboundMessage, content: str) -> str | None:
        """Reply to an inbound message on the originating platform."""
        try:
            connector = self.registry.get(original.platform)
        except KeyError:
            logger.error(f"No connector for platform: {original.platform}")
            return None

        if not connector.supports(ConnectorCapability.SEND_MESSAGE):
            logger.error(f"Connector {original.platform} cannot send messages")
            return None

        outbound = OutboundMessage(
            content=content,
            platform=original.platform,
            recipient_id=original.sender_id,
            thread_id=original.thread_id,
            channel_id=original.channel_id,
        )

        try:
            message_id = await connector.send(outbound)
            logger.info(f"Replied on {original.platform}: {message_id}")
            return message_id
        except Exception as e:
            logger.error(f"Failed to reply on {original.platform}: {e}")
            return None

    async def broadcast(
        self,
        content: str,
        platforms: list[str],
        channel_ids: dict[str, str] | None = None,
    ) -> dict[str, str | None]:
        """
        Send a message to multiple platforms.

        Args:
            content: Message content
            platforms: List of platform IDs to send to
            channel_ids: Optional mapping of platform -> channel_id

        Returns:
            Mapping of platform -> message_id (None if failed)
        """
        channel_ids = channel_ids or {}
        results: dict[str, str | None] = {}

        for platform in platforms:
            try:
                connector = self.registry.get(platform)
            except KeyError:
                logger.warning(f"No connector for platform: {platform}")
                results[platform] = None
                continue

            if not connector.supports(ConnectorCapability.SEND_MESSAGE):
                logger.warning(f"Connector {platform} cannot send messages")
                results[platform] = None
                continue

            outbound = OutboundMessage(
                content=content,
                platform=platform,
                recipient_id=channel_ids.get(platform, ""),
                channel_id=channel_ids.get(platform),
            )

            try:
                message_id = await connector.send(outbound)
                results[platform] = message_id
                logger.info(f"Broadcast to {platform}: {message_id}")
            except Exception as e:
                logger.error(f"Failed to broadcast to {platform}: {e}")
                results[platform] = None

        return results

    async def health_check(self) -> dict[str, bool]:
        """Check health of all messaging connectors."""
        results: dict[str, bool] = {}

        for connector in self.registry.get_messaging_connectors():
            try:
                results[connector.connector_id] = await connector.health()
            except Exception:
                results[connector.connector_id] = False

        return results
