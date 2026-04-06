import enum
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship

from database import Base, Mapped, mapped_column, str_uniq


class UpdateOrder(Base):
    ticker: Mapped[str]
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete='CASCADE'))
    exchange: Mapped['Exchange'] = relationship(back_populates="updateorders")
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete='CASCADE'))
    trade: Mapped['Trade'] = relationship(back_populates='updateorders')
    new_sl: Mapped[float]


class Side(enum.Enum):
    buy = "Buy"
    sell = "Sell"


class DefaultOrder(Base):
    ticker: Mapped[str]
    side: Mapped[Side]
    size: Mapped[float]
    request_price: Mapped[float]
    stop_loss: Mapped[float] = mapped_column(nullable=True)
    market: Mapped[bool]
    create_time: Mapped[datetime]
    filled_time: Mapped[datetime]
    filled_price: Mapped[float] = mapped_column(nullable=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete='CASCADE'))
    trade: Mapped['Trade'] = relationship(back_populates='defaultorders')
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete='CASCADE'))
    exchange: Mapped['Exchange'] = relationship(back_populates="defaultorders")


class Trade(Base):
    ticker: Mapped[str]
    defaultorders: Mapped[list['DefaultOrder']] = relationship(back_populates="trade")
    updateorders: Mapped[list['UpdateOrder']] = relationship(back_populates="trade")
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete='CASCADE'))
    exchange: Mapped['Exchange'] = relationship(back_populates="trades")
    pnl: Mapped[float] = mapped_column(nullable=True)

    def __repr__(self):
        return str({
            'ticker': self.ticker,
            'pnl': self.pnl
        })


class Renko(Base):
    ticker: Mapped[str]
    datetime: Mapped[datetime]
    o: Mapped[float]
    h: Mapped[float]
    l: Mapped[float]
    c: Mapped[float]
    v: Mapped[float]
    size: Mapped[float]
    duration: Mapped[float]= mapped_column(nullable=True)
    buy: Mapped[bool]
    sell: Mapped[bool]
    buy_proba: Mapped[float] = mapped_column(nullable=True)
    sell_proba: Mapped[float] = mapped_column(nullable=True)
    exchange_id: Mapped[int] = mapped_column(ForeignKey("exchanges.id", ondelete='CASCADE'))
    exchange: Mapped['Exchange'] = relationship(back_populates="renkos")


class Exchange(Base):
    name: Mapped[str_uniq]
    updateorders: Mapped[list['UpdateOrder']] = relationship(back_populates="exchange")
    defaultorders: Mapped[list['DefaultOrder']] = relationship(back_populates="exchange")
    trades: Mapped[list['Trade']] = relationship(back_populates="exchange")
    renkos: Mapped[list['Renko']] = relationship(back_populates='exchange')
