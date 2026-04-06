import asyncio
import logging
import struct
import traceback
from datetime import datetime
from typing import Optional

from packages import BybitMarketOrder, BybitUpdateOrder, SpeedMonitor, TimeMonitor, RenkoCandle

from models import Renko
from orm import ORM


logger = logging.getLogger(__name__)
tm = TimeMonitor()
sm = SpeedMonitor()


class BybitDatabaseReader:
    '''
        Читает запрашиваемые данные из БД и отдаёт.
    '''
    @staticmethod
    async def get_last_renko(ticker: str, start_from: datetime):
        try:
            result = await ORM.get_renko_filtered(Renko.datetime > start_from, ticker=ticker)
            return result
        
        except Exception as e:
            logger.error(f'Ошибка при добыче ренко: {e}')
            logger.error(traceback.format_exc())
            return

    @staticmethod
    async def get_trades():
        try:
            result = await ORM.get_trades_filtered()
            return result
        
        except Exception as e:
            logger.error(f'Ошибка при добыче сделок: {e}')
            logger.error(traceback.format_exc())
            return


class BybitDatabaseWriter:
    '''
        Пишет свечи и сделки робота в базу данных. Сделки собирает из последовательности ордеров. Данные получает из NATS JetStream.
        -------------------------------------------------------------
        name: str - наименование биржи, откуда поступают данные.
    '''
    def __init__(self, name: str = 'bybit'):
        self._name = name
        self._exchange_id = None
        self._orders = {}
        self._order_lock = asyncio.Lock()
        self._init_done = False
        self._init_lock = asyncio.Lock()
        self._reader = BybitDatabaseReader()
        # self._incoming_history = []

    @tm.measure_time('metrics.latency.databasewriter.handleorder')
    async def handle_order(self, message) -> None:
        '''
            Callback для NATS JetStream для обработки сообщения с ордером.
        '''
        if not self._init_done:
            async with self._init_lock:
                await ORM.create_tables()
                self._exchange_id = await ORM.add_exchange(name=self._name)
                self._init_done = True

        try:
            order = BybitMarketOrder.from_bytes(data=message.data)
            
        except struct.error:
            order = BybitUpdateOrder.from_bytes(data=message.data)

        except Exception as e:
            logger.error(f'Ошибка при парсинге ордера {message.data} {e} {traceback.format_exc()}')

        await self._add_order(order=order)

    @tm.measure_time('metrics.latency.databasewriter.handlerenko')
    async def handle_renko(self, message) -> None:
        '''
            Callback для NATS JetStream для обработки сообщения со свечой.
        '''
        if not self._init_done:
            async with self._init_lock:
                await ORM.create_tables()
                self._exchange_id = await ORM.add_exchange(name=self._name)
                self._init_done = True

        renko = RenkoCandle.from_bytes(data=message.data)

        await self._add_renko(renko=renko)
        
    async def _add_order(self, order: Optional[BybitMarketOrder | BybitUpdateOrder]) -> None:
        async with self._order_lock:
            if self._orders.get(order.ticker):
                if order.position_id != self._orders[order.ticker][0].position_id:
                    logger.error(f'По тикеру {order.ticker} пришел ордер с id позиции, отличным от накопленных ордеров. При этом они в бд не отправились')
                    return

                self._orders[order.ticker].append(order)

                if order.is_final:
                    asyncio.create_task(ORM.add_trade(orders=self._orders[order.ticker], exchange_id=self._exchange_id))
                    del self._orders[order.ticker]

                    logger.info(f'СОХРАНИЛ {order.ticker}')
            
            else:
                if order.is_final:
                    logger.error(f'Первый же ордер по тикеру {order.ticker} оказался финальным')
                    return
                
                self._orders[order.ticker] = [order]

    async def _add_renko(self, renko: RenkoCandle) -> None:
        async with self._order_lock:
            asyncio.create_task(ORM.add_renko(renko=renko, exchange_id=self._exchange_id))
    
