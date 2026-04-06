import logging
from typing import List

import nats

logger = logging.getLogger(__name__)


class NATSPublisher:
    '''
        Публикует сообщения в NATS Core.
    '''
    def __init__(self):
        self._client = None

    async def connect(self, url: str, port: int = 4222) -> None:
        address = f'nats://{url}:{port}'
        self._client = await nats.connect(address)

    async def publish(self, subject: str, data) -> None:
        if self._client:
            await self._client.publish(subject, data)


class JetStreamPublisher:
    '''
        Публикует сообщения в NATS JetStream.
    '''
    def __init__(self):
        self._client = None
        self._jetstream = None

    async def connect(self, url: str, port: int = 4223) -> None:
        address = f'nats://{url}:{port}'
        self._client = await nats.connect(address)
        self._jetstream = self._client.jetstream(timeout=60)

    async def add_stream(self, name: str, subjects: List[str], **kwargs) -> None:
        await self._jetstream.add_stream(
            name=name,
            subjects=subjects,
            **kwargs
        )

    async def publish(self, subject: str, data) -> None:
        if self._jetstream:
            try:
                await self._jetstream.publish(subject, data)
            
            except nats.errors.MaxPayloadError:
                logger.error(f'Превысил максимальную полезную нагрузку: {subject}')
