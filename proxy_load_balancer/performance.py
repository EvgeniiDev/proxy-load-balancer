import asyncio
import time
import psutil
import logging
from typing import Dict, Any
from collections import deque


class PerformanceMonitor:
    """Мониторинг производительности для оптимизации"""
    
    def __init__(self, max_samples: int = 1000):
        self.max_samples = max_samples
        self.response_times = deque(maxlen=max_samples)
        self.request_count = 0
        self.error_count = 0
        self.start_time = time.time()
        self.last_stats_time = time.time()
        self.last_request_count = 0
        self.logger = logging.getLogger("performance")
        
    def record_request(self, response_time: float, is_error: bool = False):
        """Записать метрики запроса"""
        self.response_times.append(response_time)
        self.request_count += 1
        if is_error:
            self.error_count += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистики производительности"""
        current_time = time.time()
        uptime = current_time - self.start_time
        
        # Вычисление RPS
        time_diff = current_time - self.last_stats_time
        requests_diff = self.request_count - self.last_request_count
        rps = requests_diff / time_diff if time_diff > 0 else 0
        
        # Обновляем для следующего вычисления
        self.last_stats_time = current_time
        self.last_request_count = self.request_count
        
        # Метрики времени ответа
        response_stats = {}
        if self.response_times:
            sorted_times = sorted(self.response_times)
            count = len(sorted_times)
            response_stats = {
                'avg': sum(sorted_times) / count,
                'min': sorted_times[0],
                'max': sorted_times[-1],
                'p50': sorted_times[count // 2],
                'p95': sorted_times[int(count * 0.95)],
                'p99': sorted_times[int(count * 0.99)]
            }
        
        # Системные метрики
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return {
            'uptime': uptime,
            'total_requests': self.request_count,
            'error_count': self.error_count,
            'error_rate': self.error_count / self.request_count if self.request_count > 0 else 0,
            'rps': rps,
            'response_time': response_stats,
            'memory': {
                'rss': memory_info.rss,
                'vms': memory_info.vms,
                'percent': process.memory_percent()
            },
            'cpu_percent': process.cpu_percent(),
            'open_files': len(process.open_files()) if hasattr(process, 'open_files') else 0,
            'connections': len(process.connections()) if hasattr(process, 'connections') else 0
        }
    
    def log_performance_stats(self):
        """Логирование статистик производительности"""
        stats = self.get_stats()
        
        self.logger.info(
            f"Performance: {stats['rps']:.1f} RPS, "
            f"{stats['total_requests']} total requests, "
            f"error rate: {stats['error_rate']:.2%}, "
            f"avg response: {stats['response_time'].get('avg', 0):.3f}s, "
            f"memory: {stats['memory']['percent']:.1f}%"
        )
        
        return stats


def create_performance_middleware(monitor: PerformanceMonitor):
    """Создает middleware для автоматического мониторинга производительности"""
    
    async def performance_middleware(request, handler):
        start_time = time.time()
        
        try:
            response = await handler(request)
            response_time = time.time() - start_time
            is_error = response.status >= 400
            
            monitor.record_request(response_time, is_error)
            
            # Добавляем заголовки производительности
            response.headers['X-Response-Time'] = f"{response_time:.3f}"
            
            return response
            
        except Exception as e:
            response_time = time.time() - start_time
            monitor.record_request(response_time, True)
            raise
    
    return performance_middleware


async def start_performance_monitoring(monitor: PerformanceMonitor, interval: int = 60):
    """Запуск периодического мониторинга"""
    while True:
        await asyncio.sleep(interval)
        monitor.log_performance_stats()
