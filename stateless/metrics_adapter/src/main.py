import logging
import time
import traceback

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from packages import NATSConsumer
from prometheus_client import generate_latest
import uvicorn

from metrics_registry import handle_metric_message
from config import settings

logging.basicConfig(
    level=settings.logging_level,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


app = FastAPI(title='MetricsAdapter')
consumer = NATSConsumer()

@app.get('/metrics')
async def get_metrics():
    '''
        Отдает метрики Prometheus по запросу.
    '''
    try:
        return Response(generate_latest(), media_type="text/plain")
    
    except Exception as e:
        logger.error(f'Ошибка при отправке метрик: {e}')
        logger.debug(traceback.format_exc())

@app.on_event('startup')
async def startup():

    try:
        await consumer.connect(url=settings.NATS_HOST, port=settings.NATS_PORT)
        await consumer.subscribe(topic="metrics.>", callback=handle_metric_message)
        logger.info('Успешно подключился к NATS')

    except Exception as e:
        logger.error(f'Подключение к NATS не удалось: {e}')
        logger.debug(traceback.format_exc())
        raise e

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.FASTAPI_PORT, reload=False)
    