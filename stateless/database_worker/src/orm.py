from sqlalchemy import select
from packages import BybitMarketOrder, BybitUpdateOrder, RenkoCandle

from database import Base, engine, session_factory
from models import DefaultOrder, Exchange, Trade, Renko, UpdateOrder, Side


def calculate_pnl(all_orders: list[BybitMarketOrder|BybitUpdateOrder]) -> float:
    buy_queue = []
    total_pnl = 0.0
    orders = []
    for order in all_orders:
        if order.is_market_order:
            orders.append(order)

    sorted_orders = sorted(orders, key=lambda x: x.filled_time)
    
    for order in sorted_orders:            
        if order.side == "Buy":
            buy_queue.append([order.size, order.filled_price])
            
        elif order.side == "Sell":
            remaining_sell = order.size
            
            while remaining_sell > 0 and buy_queue:
                buy_size, buy_price = buy_queue[0]
                
                if buy_size <= remaining_sell:
                    total_pnl += buy_size * (order.filled_price - buy_price)
                    remaining_sell -= buy_size
                    buy_queue.pop(0)
                else:
                    total_pnl += remaining_sell * (order.filled_price - buy_price)
                    buy_queue[0][0] -= remaining_sell
                    remaining_sell = 0
    
    return total_pnl


class ORM:
    '''
        Взаимодействует с БД. Пересоздаёт таблицы, добавляет и читает данные.
    '''
    @staticmethod
    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    @staticmethod
    async def add_exchange(name: str = 'bybit'):
        async with session_factory() as session:
            exchange = Exchange(name=name)
            session.add(exchange)
            await session.flush()
            exchange_id = exchange.id
            await session.commit()
        return exchange_id

    @staticmethod
    async def add_trade(orders: list[BybitUpdateOrder|BybitMarketOrder], exchange_id: int):
        async with session_factory() as session:
            pnl = calculate_pnl(all_orders=orders)
            models = []
            trade = Trade(ticker=orders[0].ticker, exchange_id=exchange_id, pnl=pnl)
            session.add(trade)
            await session.flush()
            for order in orders:
                if order.is_market_order:
                    models.append(
                        DefaultOrder(
                            ticker=order.ticker, 
                            side=Side(order.side), 
                            size=order.size, 
                            request_price=order.request_price, 
                            stop_loss=order.stop_loss,
                            market=order.market,
                            create_time=order.create_time,
                            filled_time=order.filled_time,
                            filled_price=order.filled_price,
                            trade_id=trade.id,
                            exchange_id=exchange_id
                        )
                    )
                else:
                    models.append(
                        UpdateOrder(
                            ticker=order.ticker,
                            exchange_id=exchange_id,
                            trade_id=trade.id,
                            new_sl=order.new_sl
                        )
                    )

            session.add_all(models)
            await session.flush()
            await session.commit()

    @staticmethod
    async def add_renko(renko: RenkoCandle, exchange_id: int):
        model = Renko(
            ticker=renko.symbol,
            datetime=renko.datetime,
            o=renko.o,
            h=renko.h,
            l=renko.l,
            c=renko.c,
            v=renko.v,
            size=renko.size,
            duration=renko.duration,
            buy=renko.buy,
            sell=renko.sell,
            buy_proba=renko.buy_proba,
            sell_proba=renko.sell_proba,
            exchange_id=exchange_id
        )
        
        async with session_factory() as session:
            session.add(model)
            await session.flush()
            await session.commit()
    
    @staticmethod
    async def get_trades_filtered(**filter_by):
        async with session_factory() as session:
            query = select(Trade).filter_by(**filter_by)
            res = await session.execute(query)
        return res.scalars().all()
    
    @staticmethod
    async def get_renko_filtered(*filters, **filter_by):
        async with session_factory() as session:
            query = select(Renko)
            
            if filters:
                query = query.filter(*filters)
            
            if filter_by:
                query = query.filter_by(**filter_by)
            
            res = await session.execute(query)
            return res.scalars().all()
