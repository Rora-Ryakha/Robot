from typing import Coroutine

import nats


class NATSConsumer:
    '''
        Подписывается на топики в NATS Core и получает сообщения, вызывая указанный в subscribe callback.
    '''
    def __init__(self):
        self._client = None

    async def connect(self, url: str, port: int = 4222) -> None:
        address = f'nats://{url}:{port}'
        self._client = await nats.connect(address)

    async def subscribe(self, topic: str, callback: Coroutine) -> None:
        await self._client.subscribe(topic, cb=callback)


class JetStreamConsumer:
    '''
        Подписывается на топики в NATS JetStream и получает сообщения, вызывая указанный в subscribe callback.
    '''
    def __init__(self):
        self._client = None
        self._jetstream  = None

    async def connect(self, url: str, port: int = 4223) -> None:
        address = f'nats://{url}:{port}'
        self._client = await nats.connect(address)
        self._jetstream = self._client.jetstream()

    async def subscribe(self, topic: str, callback: Coroutine) -> None:
        await self._jetstream.subscribe(topic, cb=callback)
