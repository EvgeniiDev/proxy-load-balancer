import time
import threading
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict, deque


class ProxyStats:
    """Класс для сбора и анализа статистики прокси"""

    def __init__(self, max_history: int = 1000) -> None:
        self.max_history = max_history
        self.request_history = defaultdict(lambda: deque(maxlen=max_history))
        self.success_counts = defaultdict(int)
        self.failure_counts = defaultdict(int)
        self.response_times = defaultdict(lambda: deque(maxlen=100))
        self.lock = threading.Lock()
        
    def record_request(self, proxy_key: str, success: bool, response_time: float = 0) -> None:
        """Записать результат запроса"""
        with self.lock:
            timestamp = time.time()
            self.request_history[proxy_key].append({
                'timestamp': timestamp,
                'success': success,
                'response_time': response_time
            })
            
            if success:
                self.success_counts[proxy_key] += 1
                if response_time > 0:
                    self.response_times[proxy_key].append(response_time)
            else:
                self.failure_counts[proxy_key] += 1
    
    def get_proxy_stats(self, proxy_key: str) -> Dict[str, Any]:
        """Получить статистику для конкретного прокси"""
        with self.lock:
            total_requests: int = self.success_counts[proxy_key] + self.failure_counts[proxy_key]
            success_rate: float = (self.success_counts[proxy_key] / total_requests * 100) if total_requests > 0 else 0
            
            response_times: List[float] = list(self.response_times[proxy_key])
            avg_response_time: float = sum(response_times) / len(response_times) if response_times else 0
            
            return {
                'proxy': proxy_key,
                'total_requests': total_requests,
                'successful_requests': self.success_counts[proxy_key],
                'failed_requests': self.failure_counts[proxy_key],
                'success_rate': round(success_rate, 2),
                'avg_response_time': round(avg_response_time, 3)
            }
    
    def get_all_stats(self) -> List[Dict[str, Any]]:
        """Получить статистику всех прокси"""
        with self.lock:
            all_proxies = set(self.success_counts.keys()) | set(self.failure_counts.keys())
            return [self.get_proxy_stats(proxy) for proxy in all_proxies]
    
    def get_recent_activity(self, proxy_key: str, minutes: int = 5) -> List[Dict[str, Any]]:
        """Получить недавнюю активность прокси"""
        with self.lock:
            cutoff_time: float = time.time() - (minutes * 60)
            recent_requests: List[Dict[str, Any]] = [
                req for req in self.request_history[proxy_key]
                if req['timestamp'] > cutoff_time
            ]
            return recent_requests
    
    def reset_stats(self, proxy_key: Optional[str] = None) -> None:
        """Сбросить статистику"""
        with self.lock:
            if proxy_key:
                self.success_counts[proxy_key] = 0
                self.failure_counts[proxy_key] = 0
                self.request_history[proxy_key].clear()
                self.response_times[proxy_key].clear()
            else:
                self.success_counts.clear()
                self.failure_counts.clear()
                self.request_history.clear()
                self.response_times.clear()


class ProxyMonitor:
    """Монитор для отслеживания состояния прокси в реальном времени"""
    
    def __init__(self, balancer: Any) -> None:
        self.balancer: Any = balancer
        self.stats: ProxyStats = ProxyStats()
        self.is_monitoring: bool = False
        self.monitor_thread: Optional[threading.Thread] = None
    
    def start_monitoring(self) -> None:
        """Запустить мониторинг"""
        if self.is_monitoring:
            return
        
        self.is_monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self) -> None:
        """Остановить мониторинг"""
        self.is_monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
    
    def _monitor_loop(self) -> None:
        """Основной цикл мониторинга"""
        while self.is_monitoring:
            try:
                self._collect_stats()
                time.sleep(10)
            except Exception:
                pass
    
    def _collect_stats(self) -> None:
        """Сбор текущей статистики"""
        try:
            from .utils import ProxyManager
            with self.balancer.lock:
                for proxy in self.balancer.available_proxies:
                    proxy_key = ProxyManager.get_proxy_key(proxy)
                    self.stats.record_request(proxy_key, True, 0)
        except ImportError:
            pass
    
    def get_monitoring_report(self) -> Dict[str, Any]:
        """Получить отчет мониторинга"""
        balancer_stats: Dict[str, int] = self.balancer.get_stats()
        proxy_stats: List[Dict[str, Any]] = self.stats.get_all_stats()
        
        return {
            'timestamp': time.time(),
            'balancer': balancer_stats,
            'proxies': proxy_stats,
            'top_performer': self._get_top_performer(),
            'worst_performer': self._get_worst_performer()
        }
    
    def _get_top_performer(self) -> Dict[str, Any]:
        """Найти лучший прокси по производительности"""
        stats: List[Dict[str, Any]] = self.stats.get_all_stats()
        if not stats:
            return {}
        
        return max(stats, key=lambda x: (x['success_rate'], -x['avg_response_time']))
    
    def _get_worst_performer(self) -> Dict[str, Any]:
        """Найти худший прокси по производительности"""
        stats: List[Dict[str, Any]] = self.stats.get_all_stats()
        if not stats:
            return {}
        
        return min(stats, key=lambda x: (x['success_rate'], -x['avg_response_time']))
