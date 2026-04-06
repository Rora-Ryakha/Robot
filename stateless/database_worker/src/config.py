import logging

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASS: str
    DB_NAME: str
    JETSTREAM_HOST: str
    JETSTREAM_PORT: int
    VERBOSITY: str
    FASTAPI_PORT: int
    NATS_HOST: str
    NATS_PORT: int
    log_map: dict = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}

    @property
    def DATABASE_URL_asyncpg(self):
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
    
    @property
    def logging_level(self):
        try:
            return self.log_map[self.VERBOSITY]
    
        except:
            raise Exception(f'Укажите VERBOSITY равную одному из вариантов: {self.log_map.keys()}')
    
    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()