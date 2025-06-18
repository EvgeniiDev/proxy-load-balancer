import collections
import logging
import threading
import time
from typing import Any, Dict, List, Deque
from .balancer import ProxyBalancer
from .utils import ProxyManager

class ProxyMonitor:
    def __init__(self, balancer: ProxyBalancer, max_history: int = 100):
        self.balancer = balancer
        self.is_monitoring = False
        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.stats_history: Deque[Dict[str, Any]] = collections.deque(maxlen=max_history)
        self.proxy_stats: Dict[str, Dict[str, Any]] = {}
        self.stats_lock = threading.RLock()
        self.logger = logging.getLogger("proxy_monitor")
        self._setup_logger()
    def _setup_logger(self):
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    def start_monitoring(self):
        if self.is_monitoring:
            return
        self.is_monitoring = True
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.logger.info("Proxy monitoring started")
    def stop_monitoring(self):
        if not self.is_monitoring:
            return
        self.is_monitoring = False
        self.stop_event.set()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        self.logger.info("Proxy monitoring stopped")
    def _monitor_loop(self):
        interval = 10
        try:
            interval = self.balancer.config.get("monitoring_interval", 10)
        except (AttributeError, KeyError):
            pass
        while not self.stop_event.wait(interval):
            try:
                self._collect_stats()
            except Exception as e:
                self.logger.error(f"Error collecting stats: {str(e)}")
    def _collect_stats(self):
        with self.stats_lock:
            balancer_stats = self.balancer.get_stats()
            timestamp = time.time()
            proxy_stats = []
            with self.balancer.lock:
                all_proxies = self.balancer.available_proxies + self.balancer.unavailable_proxies
                for proxy in all_proxies:
                    proxy_key = ProxyManager.get_proxy_key(proxy)
                    is_available = proxy in self.balancer.available_proxies
                    failures = self.balancer.failure_counts.get(proxy_key, 0)
                    proxy_info = {
                        "host": proxy["host"],
                        "port": proxy["port"],
                        "status": "available" if is_available else "unavailable",
                        "failures": failures
                    }
                    proxy_stats.append(proxy_info)
                    if proxy_key not in self.proxy_stats:
                        self.proxy_stats[proxy_key] = {
                            "total_failures": 0,
                            "last_status_change": timestamp
                        }
                    if is_available != (self.proxy_stats[proxy_key].get("last_status", "") == "available"):
                        self.proxy_stats[proxy_key]["last_status_change"] = timestamp
                    self.proxy_stats[proxy_key]["last_status"] = "available" if is_available else "unavailable"
                    self.proxy_stats[proxy_key]["total_failures"] += failures - self.proxy_stats[proxy_key].get("last_failures", 0)
                    self.proxy_stats[proxy_key]["last_failures"] = failures
            snapshot = {
                "timestamp": timestamp,
                "balancer_stats": balancer_stats,
                "proxy_stats": proxy_stats
            }
            self.stats_history.append(snapshot)
            self.logger.info(f"Stats collected: {snapshot}")
