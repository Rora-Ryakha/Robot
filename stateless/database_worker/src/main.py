import asyncio
import logging

from fastapi import FastAPI
from packages import JetStreamConsumer, NATSPublisher

from database_worker import BybitDatabaseReader, BybitDatabaseWriter, sm, tm
from config import settings

logging.basicConfig(
    level=settings.logging_level,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# app = FastAPI()

async def main():
    publisher = NATSPublisher()
    await publisher.connect(url=settings.NATS_HOST, port=settings.NATS_PORT)
    tm.add_publisher(publisher=publisher)
    sm.add_publisher(publisher=publisher)
    consumer = JetStreamConsumer()
    writer = BybitDatabaseWriter()
    await consumer.connect(url=settings.JETSTREAM_HOST, port=settings.JETSTREAM_PORT)
    await consumer.subscribe('renko', callback=writer.handle_renko)
    await consumer.subscribe('orders', callback=writer.handle_order)
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
