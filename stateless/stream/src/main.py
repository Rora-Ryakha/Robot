import asyncio
import logging
import traceback

from packages import ExtendedAsyncWebsocketClient, NATSPublisher
from pybit.asyncio.unified_trading import AsyncHTTP

from bybit_stream import BybitOrderStreamProcessor, BybitStream, BybitTradeStreamProcessor, sm, tm
from config import settings

logging.basicConfig(
    level=settings.logging_level,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    publisher = NATSPublisher()
    await publisher.connect(url=settings.NATS_HOST, port=settings.NATS_PORT)
    tm.add_publisher(publisher=publisher)
    sm.add_publisher(publisher=publisher)
    public_websocket = ExtendedAsyncWebsocketClient(channel_type='linear', testnet=settings.TESTNET)
    private_websocket = ExtendedAsyncWebsocketClient(channel_type='private', testnet=settings.TESTNET, demo=settings.DEMO, api_key=settings.API_KEY, api_secret=settings.API_SECRET)
    trade_processor = BybitTradeStreamProcessor(publisher=publisher)
    order_processor = BybitOrderStreamProcessor(publisher=publisher)

    session = AsyncHTTP(testnet=settings.TESTNET, demo=settings.DEMO, api_key=settings.API_KEY, api_secret=settings.API_SECRET)

    stream = BybitStream(
        public_websocket=public_websocket, 
        private_websocket=private_websocket, 
        trade_processor=trade_processor, 
        order_processor=order_processor, 
        session=session,
        update_interval_min=settings.UPDATE_INTERVAL_MIN
    )

    await stream.run()

    while True:
        await asyncio.sleep(1)

if __name__ == '__main__':
    asyncio.run(main())