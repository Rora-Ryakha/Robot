import asyncio
import logging
import struct
import traceback
from datetime import datetime, timedelta, timezone

from packages import BybitMarketOrder, BybitUpdateOrder, JetStreamPublisher, PullbackTrailingStop, RenkoCandle, RenkoList, SpeedMonitor, TimeMonitor, Trade

from candle_worker import BybitCandleWorker
from decision_makers import BaseDecisionMaker
from Indicators import IndicatorsProcessor
from initializer import Initializer
from order_managers import BaseOrderManager
from position_managers import BasePositionManager
from predictor import Predictor
from renko_builder import RenkoBuilder
from ticker_processor import TickerProcessor


logger = logging.getLogger(__name__)
robot_tm = TimeMonitor()
robot_sm = SpeedMonitor()


class Robot: # TODO: в initialization дописать подтягивание актуальной свечи из бд для репликации
    '''
        Собственно основной класс робота. В нем есть callbackи для клиента NATS Core, обрабатывающие сделки и ордеры. Если сделка по этому тикеру приходит 
        впервые, запускается инициализация тикера - подгрузка свечек и информации о тикере, преобразование исторических свечек в ренко. 
        Следующие сделки обрабатываются одинаково - строим ренко-свечи, получаем новые - генерируем предикт по ним, дальше в зависимости от предикта
        и текущей позиции принимается решение, далее пытаемся подготовить соответствующий ордер и исполнить его. Созданные свечки отправляются на сохранение
        в бд через брокер сообщений.
        Обработка ордеров происходит примитивно: если с биржи пришел ордер, значит наш ордер где-то имполнился. Мы сопоставляем его с локальной позицией, 
        принимаем дополнительное решение, если надо, и обновляем локальную позицию. Ордер передаем через брокер сообщений в БД.
        -----------------------------------------------------------------------
        candle_worker - модуль для парсинга исторических свечек и информации о тикеру. Должен иметь методы get_candles_until_trade и get_ticker_info. Подробнее
        в докстрингах по нему
        predictor - модуль для генерации предиктов вида [вероятность False, вероятность True] для покупки и продажи. 
        order_manager - модуль для управления ордерами. Должен иметь методы execute для отправки ордера и set_leverage для установки плеча по тикеру
        position_manager - модуль для управления позициями. Должен иметь методы prepare_order, create_position, update_position, close_position. 
        Подробнее в доках по PositionManager_v1
        decision_maker - модуль для принятия решений на основе вероятностей покупки/продажи, свечи и других параметров. Должен иметь метод decide. Подробнее
        в коде и докстрингах по DecisionMaker_v1
        publisher - модуль для отправки сообщений в NATS JetStream.   
    '''
    def __init__(self, candle_worker: BybitCandleWorker, predictor: Predictor, order_manager: BaseOrderManager, position_manager: BasePositionManager, decision_maker: BaseDecisionMaker, publisher: JetStreamPublisher, leverage: float = 5.0):
        self._predictor = predictor
        self._candle_worker = candle_worker
        self._order_manager = order_manager
        self._position_manager = position_manager
        self._decision_maker = decision_maker
        self._publisher = publisher
        self._leverage = leverage

        self._tickers_info = {}
        self._ticker_processors = {}
        self._ticker_locks = {}
        self._position_lock = asyncio.Lock()
        self._init_active = {}
        self._update_active = {}
        self._margin_mode_set = None
        self._last_latency_warning = datetime.now()

    async def _initialization(self, trade: Trade, ticker: str, set_margin_mode: bool = False) -> None:
        try:
            if set_margin_mode:
                result = await self._candle_worker.set_margin_mode(mode='ISOLATED_MARGIN')
                if result != True:
                    logger.error(f'Ошибка при настройке Margin Mode: {result}')
                else:
                    self._margin_mode_set = True

            initializer = Initializer(ticker=ticker, candle_worker=self._candle_worker, category='linear')
            historical_renko, current_renko, ticker_info = await initializer.initialize(last_trade=trade)

            if ticker_info.is_prelisting or historical_renko == 'NODATA':
                logger.info(f'NODATA {ticker}')
                return
            
            result = await self._order_manager.set_leverage(ticker=ticker, category=ticker_info.category, leverage=self._leverage)
            if result != True:
                logger.error(f'Не вышло настроить плечо по {ticker}: {result}')
            
            indicator_processor = IndicatorsProcessor()
            renko_builder = RenkoBuilder(current_renko=current_renko)
            trailing_stop = PullbackTrailingStop()
            
            async with self._ticker_locks[ticker]:
                self._tickers_info[ticker] = ticker_info
                self._ticker_processors[ticker] = TickerProcessor(
                    ticker=ticker,
                    renko_builder=renko_builder, 
                    predictor=self._predictor, 
                    indicator_processor=indicator_processor, 
                    initial_renko=historical_renko[:-350],
                    trailing=trailing_stop
                )
                self._init_active[ticker] = False

        except Exception as e:
            logger.error(f'Ошибка при инициализации: {e}')
            logger.error(traceback.format_exc())

    async def _publish_renko(self, renko: RenkoList) -> None:
        tasks = [asyncio.create_task(self._publisher.publish('renko', bytes(candle))) for candle in renko]
        
        await asyncio.gather(*tasks)

    async def _manage_position(self, ticker: str, buy_prediction: list, sell_prediction: list, renko: RenkoCandle, trailing: PullbackTrailingStop):
        if not self._margin_mode_set:
            return
        
        i = 0
        while not self._tickers_info.get(ticker) and self._update_active.get(ticker):
            i += 1
            logger.warning(f'По тикеру {ticker} нет инфы, а я принимаю решение. Жду {i} обновления')
            await asyncio.sleep(2)
        
        if not self._tickers_info.get(ticker):
            logger.warning(f'По тикеру {ticker} попал в управление позицией, не имея инфы по тикеру')
            return

        async with self._position_lock:
            position = self._position_manager.get_position(ticker=ticker)
            decision = await self._decision_maker.decide(
                ticker=ticker, 
                position=position, 
                buy_predict=buy_prediction, 
                sell_predict=sell_prediction, 
                renko=renko, 
                trailing=trailing
            )

            ticker_info = self._tickers_info.get(ticker)

            if ticker_info is None:
                logger.error(f'Минимальный размер позиции по тикеру {ticker} не был добыт')
                return
            
            order = self._position_manager.prepare_order(ticker_info=ticker_info, decision=decision, trailing=trailing, renko=renko)

        if not order:
            return

        asyncio.create_task(self._order_manager.execute(order))

    async def _update_ticker_info(self, ticker: str):
        self._tickers_info[ticker] = None
        initializer = Initializer(ticker=ticker, candle_worker=self._candle_worker, category='linear')
        self._tickers_info[ticker] = await initializer.get_ticker_info()
        self._update_active[ticker] = False

    @robot_tm.measure_average_time('metrics.latency.robot.handletrade')
    @robot_sm.measure_average_speed('metrics.speed.robot.handletrade')
    async def process_trade(self, message) -> None:
        try:
            trade = Trade.from_bytes(message.data)
            ticker = trade.s
            if datetime.now() - datetime.fromtimestamp(trade.ts/1000) > timedelta(minutes=1) and datetime.now() - self._last_latency_warning > timedelta(seconds=30):
                logger.warning(f'Задержка больше 1 минуты!! {datetime.now() - datetime.fromtimestamp(trade.ts/1000)} {trade}')
                self._last_latency_warning = datetime.now()

            if self._init_active.get(ticker):
                return
            
            if self._tickers_info.get(ticker):
                if self._tickers_info[ticker].is_prelisting:
                    return

            self._ticker_locks.setdefault(ticker, asyncio.Lock())

            async with self._ticker_locks[ticker]:
                if self._init_active.get(ticker):
                    return
                               
                if not self._ticker_processors.get(ticker):
                    self._init_active[ticker] = True
                    if self._margin_mode_set is None:
                        asyncio.create_task(self._initialization(trade=trade, ticker=ticker, set_margin_mode=True))
                        self._margin_mode_set = False
                        return

                    asyncio.create_task(self._initialization(trade=trade, ticker=ticker))
                    return

                else:
                    buy_prediction, sell_prediction, trailing, renko = self._ticker_processors[ticker].process_trade(trade)
                
                if self._tickers_info.get(ticker):
                    if self._tickers_info[ticker].time_updated.day != datetime.today().day and not self._update_active.get(ticker) and self._tickers_info[ticker].time_updated.month != datetime.today().month and datetime.today().day in [3, 17] and datetime.now(tz=timezone.utc).hour >= 16:
                        self._update_active[ticker] = True
                        asyncio.create_task(self._update_ticker_info(ticker=ticker))
                
                elif not self._update_active.get(ticker):
                    self._update_active[ticker] = True
                    asyncio.create_task(self._update_ticker_info(ticker=ticker))

            if buy_prediction is None or sell_prediction is None:
                return

            renko[-1].buy_proba = buy_prediction[1]
            renko[-1].sell_proba = sell_prediction[1]
            asyncio.create_task(self._publish_renko(renko=renko))
            renko = renko[-1]

            await self._manage_position(ticker=ticker, buy_prediction=buy_prediction, sell_prediction=sell_prediction, renko=renko, trailing=trailing)    

        except Exception as e:
            logger.error(f'Ошибка в обработке сделки {e}')
            logger.error(traceback.format_exc())

    async def _process_market_order(self, order: BybitMarketOrder):
        if order.side == 'Buy':
            result = self._position_manager.create_position(ticker=order.ticker, entry_order=order)
            if not result:
                logger.error(f'Не удалось создать позицию по тикеру {order.ticker}')
            else:
                logger.info(f'Успешно открыл позицию по {order.ticker}')

            update_order = self._position_manager.prepare_order(ticker_info=self._tickers_info.get(order.ticker), decision='Update')

            if update_order:
                asyncio.create_task(self._order_manager.execute(update_order))   
        
        elif order.side == "Sell":
            result = await self._position_manager.close_position(ticker=order.ticker, close_order=order)
            if not result:
                logger.error(f'Не удалось закрыть позицию по тикеру {order.ticker}')
            else:
                logger.info(f'Успешно закрыл позицию по {order.ticker}')

        else:
            logger.error(f'Непонятный side у рыночного ордера: {order.side}')

    async def _process_update_order(self, order: BybitUpdateOrder):
        result = self._position_manager.update_position(ticker=order.ticker, update_order=order)
        if not result:
            logger.error(f'Не удалось обновить позицию по {order.ticker}')
        else:
            logger.info(f'Успешно обновил позицию по {order.ticker}')

    @robot_tm.measure_time('metrics.latency.robot.handleorder')
    async def process_order(self, message) -> None:
        try:
            order = BybitMarketOrder.from_bytes(message.data)
            
        except struct.error:
            order = BybitUpdateOrder.from_bytes(message.data)

        async with self._position_lock:
            if order.is_market_order:
                await self._process_market_order(order=order)
            
            else:
                await self._process_update_order(order=order)
