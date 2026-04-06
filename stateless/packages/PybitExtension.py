import asyncio
import enum
import json
import logging
import traceback
from typing import List

import websockets as ws
from pybit import _helpers
from pybit.asyncio.unified_trading import AsyncHTTP, AsyncWebsocketClient
from pybit.asyncio.ws import AsyncWebsocketManager
from pybit._http_manager import generate_signature
from pybit.unified_trading import PRIVATE_WSS
from pybit._websocket_stream import DOMAIN_MAIN, SUBDOMAIN_MAINNET, SUBDOMAIN_TESTNET
from websockets.exceptions import ConnectionClosedError
from websockets_proxy import proxy_connect, Proxy

from .RateLimiter import RateLimiter

logger = logging.getLogger(__name__)

DEMO_SUBDOMAIN_TESTNET = "stream-demo-testnet"
DEMO_SUBDOMAIN_MAINNET = "stream-demo"
MESSAGE_TIMEOUT = 5
PRIVATE_AUTH_EXPIRE = 10


class WSState(enum.Enum):
    INITIALISING = 1
    EXITING = 2
    STREAMING = 3
    RECONNECTING = 4


class ExtendedAsyncWebsocketManager(AsyncWebsocketManager):
    '''
        Расширение AsyncWebSocketManager от pybit-asyncio, исправляющее баги в некоторых методах вендора.
        Добавляет возможность включить demo-режим, отсутствующий в исходной библиотеке, но имеющийся на бирже.
        -------------------------------------------------------------
        demo: bool - используем ли демо-режим
    '''
    def __init__(self, demo: bool = False, *args, **kwargs):
        self.demo = demo
        super().__init__(*args, **kwargs)

    async def _auth(self):
        """
        Prepares authentication signature per Bybit API specifications.
        """

        expires = _helpers.generate_timestamp() + (PRIVATE_AUTH_EXPIRE * 1000)
        param_str = f"GET/realtime{expires}"
        signature = generate_signature(
            use_rsa_authentication=self.rsa_authentication,
            secret=self.api_secret,
            param_str=param_str
        )
        # Authenticate with API.
        await self.ws.send(json.dumps({"op": "auth", "args": [self.api_key, expires, signature]}))

    async def connect(self):
        if self.ws is None or self.ws_state == WSState.RECONNECTING:
            if not self.demo:
                subdomain = SUBDOMAIN_TESTNET if self.testnet else SUBDOMAIN_MAINNET
            else:
                subdomain = DEMO_SUBDOMAIN_TESTNET if self.testnet else DEMO_SUBDOMAIN_MAINNET

            endpoint = self.url.format(SUBDOMAIN=subdomain, DOMAIN=DOMAIN_MAIN, TLD=self.tld)

            if self.proxy:
                logger.info(f"Connected via: {self.proxy}")
                self._conn = proxy_connect(endpoint,
                                           close_timeout=0.1,
                                           open_timeout=60,
                                           ping_interval=None,
                                           ping_timeout=None,
                                           proxy=Proxy.from_url(self.proxy))
            else:
                logger.info("Connected without proxies")
                self._conn = ws.connect(endpoint,
                                        close_timeout=0.1,
                                        open_timeout=60,
                                        ping_timeout=None,  # We use custom ping task
                                        ping_interval=None)
            try:
                self.ws = await self._conn.__aenter__()
            except Exception as e:
                traceback.print_exc()
                await self._reconnect()
                logger.error(f"Connecting error: {e}")
                return
            # Authenticate for private channels
            if self.api_key and self.api_secret:
                await self._auth()

            # subscribe to channels
            for mes in self.subscription_message:
                await self.ws.send(mes)
        self._reconnects = 0
        self.ws_state = WSState.STREAMING
        logger.info("Connected successfully")
        if self.downtime_callback is not None:
            try:
                self.downtime_callback()
            except Exception as e:
                logger.error("Downtime callback error")
                traceback.print_exc()
        if self._keepalive:
            self._keepalive.cancel()
        self._keepalive = asyncio.create_task(self._keepalive_task())
        if not self._handle_read_loop:
            self._handle_read_loop = self._loop.call_soon_threadsafe(asyncio.create_task, self._read_loop())

    async def _read_loop(self):
        logger.info("Start loop.")
        while True:
            try:
                match self.ws_state:
                    case WSState.STREAMING:
                        res = await asyncio.wait_for(self.ws.recv(), timeout=MESSAGE_TIMEOUT)
                        res = self._handle_message(res)
                        if res.get("op") == "pong" or res.get("op") == "ping":
                            continue
                        if res.get("op") == "subscribe":
                            if res.get("success") is not None and res["success"] is False:
                                if res.get("ret_msg") == "Request not authorized":
                                    logger.warning(f"Cancel task because request: {res}")
                                    raise asyncio.CancelledError()
                                logger.error(f"False connecting: {res}")
                                self.ws_state = WSState.RECONNECTING
                            continue

                        if res:
                            await self.queue.put(res)

                    case WSState.EXITING:
                        logger.info("Exiting websocket")
                        await self.ws.close()
                        self._keepalive.cancel()
                        break
                    case WSState.RECONNECTING:
                        while self.ws_state == WSState.RECONNECTING:
                            await self._reconnect()
                    case self.ws.protocol.state.CLOSING:
                        await asyncio.sleep(0.1)
                        continue
                    case self.ws.protocol.state.CLOSED:
                        await self._reconnect()

            except asyncio.TimeoutError:
                continue
            except ConnectionResetError as e:
                logger.warning(f"Received connection reset by peer. Error: {e}. Trying to reconnect.")
                await self._reconnect()
            except asyncio.CancelledError:
                logger.warning("Cancelled Error")
                self._keepalive.cancel()
                break
            except OSError as e:
                logger.warning(f"Os Error: {e}")
                await self._reconnect()
                continue
            except ConnectionClosedError as e:
                logger.warning(f"Connection Closed Error: {e}")
                self._keepalive.cancel()
                await self._reconnect()
                continue
            except Exception as e:
                logger.warning(f"Unknown exception: {e}")
                continue


class ExtendedAsyncWebsocketClient(AsyncWebsocketClient):
    '''
        Расширение вебсокет-клиента от библиотеки pybit-asyncio, добавляющее нужные в этом коде стримы и возвращающее модифицированный WebSocketManager.
        Добавляет возможность включить demo-режим, отсутствующий в исходной библиотеке, но имеющийся на бирже.
        -------------------------------------------------------------
        demo: bool - используем ли демо-режим
    '''
    def __init__(self, demo: bool = False, *args, **kwargs):
        self.demo = demo
        super().__init__(*args, **kwargs)

    def order_stream(self) -> ExtendedAsyncWebsocketManager:
        subscription_message = json.dumps(
            {"op": "subscribe",
             "args": ["order.linear"]}
        )
        return ExtendedAsyncWebsocketManager(
            channel_type=self.channel_type,
            url=PRIVATE_WSS,
            subscription_message=[subscription_message],
            testnet=self.testnet,
            api_key=self.api_key,
            api_secret=self.api_secret,
            proxy=self.proxy,
            demo=self.demo
        )
    
    def position_stream(self) -> ExtendedAsyncWebsocketManager:
        subscription_message = json.dumps(
            {"op": "subscribe",
             "args": ["position.linear"]}
        )
        return ExtendedAsyncWebsocketManager(
            channel_type=self.channel_type,
            url=PRIVATE_WSS,
            subscription_message=[subscription_message],
            testnet=self.testnet,
            api_key=self.api_key,
            api_secret=self.api_secret,
            proxy=self.proxy,
            demo=self.demo
        )
    
    def any_public_stream(self, symbols: List[str]) -> AsyncWebsocketManager:
        return self.futures_kline_stream(symbols=symbols)


class ExtendedAsyncHTTP(AsyncHTTP):
    '''
        Расширение HTTP-клиента библиотеки pybit-asyncio. Даёт возможность интегрировать RateLimiter в запросы к бирже для соблюдения ограничений частоты
        запросов к API. 
        -------------------------------------------------------------
        rate_limiter: RateLimiter - ограничитель запросов. Должен иметь асинхронный контекстный менеджер.
    '''
    def __init__(self, rate_limiter: RateLimiter, *args, **kwargs):
        self._rate_limiter = rate_limiter
        self._api_methods = {'get_kline', 'place_order', 'get_instruments_info', 'set_margin_mode', 'set_leverage', 'get_tickers', 'set_trading_stop'}
        super().__init__(*args, **kwargs)
    
    def __getattribute__(self, name):
        attr = super().__getattribute__(name)
        
        if callable(attr) and name in self._api_methods:
            async def wrapper(*args, **kwargs):
                async with self._rate_limiter:
                    result = await attr(*args, **kwargs)
                return result
            return wrapper
        return attr