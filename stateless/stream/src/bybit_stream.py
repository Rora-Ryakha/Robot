import asyncio
import logging
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Optional

from packages import BybitMarketOrder, BybitUpdateOrder, ExtendedAsyncWebsocketClient, NATSPublisher, SpeedMonitor, TimeMonitor, Trade
from pybit.asyncio.unified_trading import AsyncHTTP
from pybit.asyncio.ws import AsyncWebsocketManager

logger = logging.getLogger(__name__)
tm = TimeMonitor()
sm = SpeedMonitor()


class TradeStreamProcessor(ABC):
    '''
        Базовый обработчик стрима сделок. Валидированные сделки группирует, если они пришли в один момент времени (т.е. объединяет в одну большую сделку) и
        передаёт в брокер NATS Core.
        -----------------------------------------------------
        publisher - модуль для отправки сообщений в NATS Core
    '''
    def __init__(self, publisher: NATSPublisher):
        self._publisher = publisher

    @abstractmethod
    async def _validate_trade(self, *args, **kwargs) -> bool:
        ...

    async def _publish_trade(self, trade: Trade) -> None:
        ticker = trade.s
        trade_bin = bytes(trade)
        await self._publisher.publish(f'trades.{ticker}', trade_bin)
        
    async def _group_trades(self, trades: list[Trade]) -> Dict[int, list]:
        grouped_trades = {}
        for trade in trades:
            grouped_trades.setdefault(str(trade.ts) + trade.s, Trade(ts=trade.ts, p=0.0, v=0.0, s=trade.s))
            grouped_trades[str(trade.ts) + trade.s] += trade
        
        for key in grouped_trades.keys():
            await self._publish_trade(grouped_trades[key])

    @abstractmethod
    async def process_trade_message(self, *args, **kwargs) -> None:
        ...


class BybitTradeStreamProcessor(TradeStreamProcessor):
    '''
        Модуль для обработки сообщений из байбита. Получает сообщение из стрима, валидирует его. Остальная логика в базовом классе.
    '''
    def __init__(self, publisher: NATSPublisher):
        super().__init__(publisher=publisher)

    async def _validate_trade(self, ts, p, v, ticker) -> bool:        
        if ts is None or p is None or v is None or ticker is None:
            return False
        
        return True

    @sm.measure_average_speed('metrics.speed.tradestream.avgspeed')
    @tm.measure_average_time('metrics.latency.tradestream.avgtime')
    async def process_trade_message(self, message) -> None:
        if not message:
            return
        
        data = message.get('data', [])
        
        if not data:
            logger.error(f'Пришли пустые данные: {message}')
            return
        
        trades = []
        
        for row in data:
            ts = row.get('T')
            volume = row.get('v')
            price = row.get('p')
            ticker = row.get('s')
            
            verified = await self._validate_trade(ts=ts, p=price, v=volume, ticker=ticker)

            if not verified:
                logger.error(f'Не получилось валидировать данные сделки: {row}')
                continue  
            
            trade = Trade(ts=int(ts), p=float(price), v=float(volume), s=ticker)
            trades.append(trade)

        await self._group_trades(trades)
        # logger.info(f'{datetime.now(), datetime.fromtimestamp(int(ts)/1000)}')


class OrderStreamProcessor(ABC):
    '''
        Базовый класс для обработки стрима ордеров. Исполненные ордера биржа передает сюда, этот метод передаёт преобразованные в 
        BybitMarketOrder/BybitUpdateOrder ордера и отправляет в NATS Core.
        ---------------------------------------------------------------
        publisher - модуль для отправки сообщений в NATS Core
    '''
    def __init__(self, publisher: NATSPublisher):
        self._publisher = publisher

    @abstractmethod
    async def process_order_message(self, *args, **kwargs) -> None:
        ...

    async def _publish_orders(self, orders: list[BybitMarketOrder | BybitUpdateOrder]) -> None:
        for order in orders:
            ticker = order.ticker
            order_bin = bytes(order)
            await self._publisher.publish(f'orders.{ticker}', order_bin)


class BybitOrderStreamProcessor(OrderStreamProcessor):
    '''
        Обрабатывает стрим ордеров байбита. Валидирует сообщения. Остальную логику выполняет базовый класс.
    '''
    def __init__(self, publisher: NATSPublisher):
        super().__init__(publisher=publisher)

    def _validate_message(self, status, ticker, side, size, request_price, filled_price, stop_loss, type, create_time, filled_time, left_qty, category, trigger_price) -> bool:
        if (
            status is None or
            ticker is None or
            side is None or
            size is None or
            request_price is None or
            filled_price is None or
            stop_loss is None or
            type is None or
            create_time is None or
            filled_time is None or
            left_qty is None or
            category is None or
            trigger_price is None
        ):
            return False
        
        return True
    
    def _prepare_order(self, msg: dict, status: str, ticker: str, side: str, size: str, request_price: str, stop_loss: str, create_time: str, filled_price: str, filled_time: str, category: str, trigger_price: str) -> Optional[BybitMarketOrder | BybitUpdateOrder]:
        stop_loss = 0 if stop_loss == '' else stop_loss
        trigger_price = 0 if trigger_price == '' else trigger_price

        stop_loss = trigger_price if stop_loss == 0 else stop_loss

        if status in 'Filled':
            order = BybitMarketOrder(
                ticker=ticker,
                side=side,
                size=float(size),
                request_price=float(request_price),
                stop_loss=float(stop_loss),
                market=True if type == 'Market' else False,
                create_time=datetime.fromtimestamp(float(create_time)/1000),
                filled_price=float(filled_price),
                filled_time=datetime.fromtimestamp(float(filled_time)/1000),
                category=category
            )
        
        elif status == 'Untriggered':
            order = BybitUpdateOrder(
                ticker=ticker,
                category=category,
                new_sl=float(stop_loss)
            )

        elif status in ['Triggered', 'Deactivated']:
            return None

        else:
            logger.info(f'По тикеру {ticker} пришел ордер со статусом {status}: {msg}')
            return None

        return order

    @tm.measure_time('metrics.latency.orderstream.time')  
    async def process_order_message(self, message):
        if not message:
            return
        
        if not message.get('data') and not message.get('op'):
            logger.error(f'В сообщении с ордером нет данных: {message}')
            return
        
        elif not message.get('data'):
            return
        
        data = message['data']
        orders = []
        for msg in data:
            category = msg.get('category')
            status = msg.get('orderStatus')
            ticker = msg.get('symbol')
            side = msg.get('side')
            size = msg.get('qty')
            request_price = msg.get('price')
            filled_price = msg.get('avgPrice')
            stop_loss = msg.get('stopLoss')
            trigger_price = msg.get('triggerPrice')
            type = msg.get('orderType')
            create_time = message.get('creationTime')
            filled_time = msg.get('updatedTime')
            left_qty = msg.get('leavesQty')

            verified = self._validate_message(
                status, 
                ticker, 
                side, 
                size, 
                request_price, 
                filled_price, 
                stop_loss, 
                type, 
                create_time, 
                filled_time, 
                left_qty, 
                category, 
                trigger_price
            )

            if not verified:
                logger.error(f'Не получилось валидировать данные ордера: {msg}')
                continue

            order = self._prepare_order(
                msg=msg,
                status=status, 
                ticker=ticker, 
                side=side, 
                size=size, 
                request_price=request_price, 
                stop_loss=stop_loss, 
                create_time=create_time, 
                filled_price=filled_price, 
                filled_time=filled_time, 
                category=category,
                trigger_price=trigger_price
            )
            
            if order:            
                orders.append(order)

        await self._publish_orders(orders)


class BybitStream:
    '''
        Менеджер стримов байбита. Обновляет список тикеров, подписывается на нужные стримы, сообщения распределяет в соответствующие обработчики. 
        --------------------------------------------------------------------------
        public_websocket - Клиент публичного вебсокета для байбита
        private_websocket - Клиент приватного вебсокета для байбита
        trade_processor - Обработчик стрима сделок
        order_processor - Обработчик стрима ордеров
        session - HTTP сессия байбита
        category: str - linear/spot/inverse/whatever
        update_interval_min: int - раз в сколько минут обновлять список тикеров
    '''
    def __init__(self, public_websocket: ExtendedAsyncWebsocketClient, private_websocket: ExtendedAsyncWebsocketClient,
                trade_processor: BybitTradeStreamProcessor, order_processor: BybitOrderStreamProcessor, session: AsyncHTTP,
                category: str = 'linear', update_interval_min: int = 240):
        self._public_ws = public_websocket
        self._private_ws = private_websocket
        self._session = session
        self._category = category
        self._trade_processor = trade_processor
        self._order_processor = order_processor
        self._order_stream = None
        self._trade_stream = None
        self._symbols = []
        self._update_interval = update_interval_min
        self._queue = asyncio.Queue()

    async def _sync_symbols(self) -> None:        
        async with self._session as session:
            tickers = await session.get_tickers(category=self._category)
            tickers_list = tickers.get('result', {}).get('list', [])
            
            symbols = {
                f'publicTrade.{ticker["symbol"]}'
                for ticker in tickers_list 
                if ticker["symbol"].endswith("USDT")
            }

            # symbols = {'BTCUSDT', 'ETHUSDT'}
            
            if not symbols: 
                logger.error('Получил пустой список тикеров')

        self._symbols = list(symbols)

    @tm.measure_time('metrics.latency.subupdate.time')
    async def _subscription_updater(self) -> None:
        while True:
            await self._sync_symbols()
            self._trade_stream = self._public_ws.any_public_stream(symbols=self._symbols)
            await asyncio.sleep(self._update_interval * 60)       

    async def _stream_reader(self, stream: AsyncWebsocketManager, stream_name: str) -> None:
        while True:
            try:
                msg = await stream.recv()
                if msg is None:
                    continue

                if stream_name == 'trades':
                    asyncio.create_task(self._trade_processor.process_trade_message(msg))
                
                elif stream_name == 'orders':
                    asyncio.create_task(self._order_processor.process_order_message(msg))
                
                else:
                    logger.error(f'Сообщение не подлежит распределению. Топик {stream_name}')
            
            except Exception as e:
                logger.error(f'Не удалось передать сообщение из {stream_name} в очередь: {e}')
                logger.error(traceback.format_exc())

    async def run(self): 
        self._order_stream = self._private_ws.order_stream()
        sync_task = asyncio.create_task(self._subscription_updater())

        while self._trade_stream is None:
            await asyncio.sleep(1)

        async with self._trade_stream as trade_stream, self._order_stream as order_stream:
            read_tasks = [
                asyncio.create_task(self._stream_reader(stream=trade_stream, stream_name='trades')),
                asyncio.create_task(self._stream_reader(stream=order_stream, stream_name='orders'))
            ]

            await asyncio.gather(*read_tasks, sync_task)
