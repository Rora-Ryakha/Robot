import logging

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    NATS_HOST: str
    NATS_PORT: int
    JETSTREAM_HOST: str
    JETSTREAM_PORT: int
    VERBOSITY: str
    API_KEY: str
    API_SECRET: str
    BUY_MODEL_PATH: str
    SELL_MODEL_PATH: str
    DEFAULT_POSITION_SIZE_USDT: int
    BUY_THRESHOLD: float
    SELL_THRESHOLD: float
    MIN_VOLUME_USDT: int
    DEMO: bool
    TESTNET: bool
    LEVERAGE: float
    log_map: dict = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}
    
    @property
    def logging_level(self):
        try:
            return self.log_map[self.VERBOSITY]
    
        except:
            raise Exception(f'Укажите VERBOSITY равную одному из вариантов: {self.log_map.keys()}')
    
    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
