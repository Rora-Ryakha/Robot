import asyncio
import logging

from packages import ExtendedAsyncHTTP, JetStreamPublisher, NATSConsumer, NATSPublisher, RateLimiter

from candle_worker import BybitCandleWorker
from decision_makers import DecisionMaker_v1
from Model import LightModel
from order_managers import BybitOrderManager
from position_managers import PositionManager_v1
from predictor import Predictor
from robot import Robot, robot_tm, robot_sm
from config import settings

logging.basicConfig(
    level=settings.logging_level,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    metrics_publisher = NATSPublisher()
    robot_sm.add_publisher(publisher=metrics_publisher)
    robot_tm.add_publisher(publisher=metrics_publisher)
    global_limiter = RateLimiter(limit=550, limit_timeframe_sec=5)
    order_limiter = RateLimiter(limit=9, limit_timeframe_sec=1)
    everything_session = ExtendedAsyncHTTP(rate_limiter=global_limiter, api_key=settings.API_KEY, api_secret=settings.API_SECRET, testnet=settings.TESTNET, demo=settings.DEMO, timeout=60)
    order_session = ExtendedAsyncHTTP(rate_limiter=order_limiter, api_key=settings.API_KEY, api_secret=settings.API_SECRET, testnet=settings.TESTNET, demo=settings.DEMO, timeout=60)
    publisher = JetStreamPublisher()
    await publisher.connect(url=settings.JETSTREAM_HOST, port=settings.JETSTREAM_PORT)
    await publisher.add_stream('robot', ['renko', 'orders'])

    buy_model = LightModel(settings.BUY_MODEL_PATH)
    sell_model = LightModel(settings.SELL_MODEL_PATH)

    position_manager = PositionManager_v1(default_size=settings.DEFAULT_POSITION_SIZE_USDT, publisher=publisher)
    decision_maker = DecisionMaker_v1(buy_threshold=settings.BUY_THRESHOLD, sell_threshold=settings.SELL_THRESHOLD, min_volume=settings.MIN_VOLUME_USDT)
    predictor = Predictor(buy_model=buy_model, sell_model=sell_model)

    consumer = NATSConsumer()
    await consumer.connect(url=settings.NATS_HOST, port=settings.NATS_PORT)

    async with everything_session, order_session:
        candle_worker = BybitCandleWorker(session=everything_session)
        order_manager = BybitOrderManager(session=order_session)

        robot = Robot(
            candle_worker=candle_worker, 
            predictor=predictor,
            order_manager=order_manager, 
            position_manager=position_manager, 
            decision_maker=decision_maker,
            publisher=publisher,
            leverage=settings.LEVERAGE
        )
        
        await consumer.subscribe('trades.>', robot.process_trade)
        await consumer.subscribe('orders.>', robot.process_order)

        while True:
            await asyncio.sleep(0.0001)


if __name__ == "__main__":
    asyncio.run(main())
