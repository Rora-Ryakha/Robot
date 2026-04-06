import logging

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

metrics_registry = {
    'latency': Gauge('trading_latency_seconds', 'Latency', ['service', 'module']),
    'speed': Gauge('flow_speed_per_second', 'Speed', ['service', 'module'])
}

async def handle_metric_message(msg):
    '''
        Callback для NATS Core для обработки сообщения с метрикой. Создаёт объекты Prometheus.
    '''
    topic = msg.subject  # формата metrics.latency.order_executor.execution
    value = float(msg.data.decode())
    
    parts = topic.split('.')
    if len(parts) != 4 or parts[0] != 'metrics':
        logger.error(f'Ошибка при обработке сообщения из NATS: что-то не то с топиком {topic}')
        return
    
    metric_type, service, module = parts[1], parts[2], parts[3]
    
    if metric_type in metrics_registry.keys():
        metric = metrics_registry[metric_type]

        if metric_type == 'histogram':
            metric.labels(service=service, module=module).observe(value)

        elif metric_type == 'counter':
            metric.labels(service=service, module=module).inc(value)

        elif metric_type in ['latency', 'speed']:
            metric.labels(service=service, module=module).set(value)
    