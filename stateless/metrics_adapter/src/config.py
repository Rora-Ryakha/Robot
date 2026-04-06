import logging

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    NATS_HOST: str
    NATS_PORT: int
    VERBOSITY: str
    FASTAPI_PORT: int
    log_map: dict = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}
    
    @property
    def logging_level(self):
        try:
            return self.log_map[self.VERBOSITY]
    
        except:
            raise Exception(f'Укажите VERBOSITY равную одному из вариантов: {self.log_map.keys()}')
    
    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()