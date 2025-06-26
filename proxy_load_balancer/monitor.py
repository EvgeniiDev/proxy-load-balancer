import collections
import logging
import threading
import time
from typing import Any, Dict, List, Deque, TYPE_CHECKING
from .utils import ProxyManager

if TYPE_CHECKING:
    from .proxy_balancer import ProxyBalancer

class ProxyMonitor:
    def __init__(self, balancer: 'ProxyBalancer', max_history: int = 100):
        self.balancer = balancer
        self.is_monitoring = False
        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.stats_history: Deque[Dict[str, Any]] = collections.deque(maxlen=max_history)
        self.proxy_stats: Dict[str, Dict[str, Any]] = {}
        self.stats_lock = threading.RLock()
        self.max_proxy_stats = 1000
        self.cleanup_interval = 300
        self.last_cleanup_time = time.time()
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
        stats_interval = 60  # Log stats every minute
        console_stats_interval = 30  # Print detailed stats to console every 30 seconds
        try:
            interval = self.balancer.config.get("monitoring_interval", 10)
            stats_interval = self.balancer.config.get("stats_log_interval", 60)
            console_stats_interval = self.balancer.config.get("console_stats_interval", 30)
        except (AttributeError, KeyError):
            pass
        
        last_stats_time = 0
        last_console_stats_time = 0
        while not self.stop_event.wait(interval):
            try:
                self._periodic_cleanup()
                self._collect_stats()
                
                current_time = time.time()
                
                # Print detailed stats to console periodically
                if current_time - last_console_stats_time >= console_stats_interval:
                    compact_mode = self.balancer.config.get("compact_console_stats", False)
                    if compact_mode:
                        self.balancer.print_compact_stats()
                    else:
                        print("\n" + "="*80)
                        print("PERIODIC PROXY STATISTICS UPDATE")
                        print("="*80)
                        self.balancer.print_stats()
                    last_console_stats_time = current_time
                
                # Log detailed stats periodically
                if current_time - last_stats_time >= stats_interval:
                    self.balancer.log_stats_summary()
                    last_stats_time = current_time
                    
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {str(e)}")
    def _collect_stats(self):
        with self.stats_lock:
            balancer_stats = self.balancer.get_stats()
            timestamp = time.time()
            proxy_stats = []
            with self.balancer.proxy_selection_lock:
                all_proxies = self.balancer.available_proxies + self.balancer.unavailable_proxies
                for proxy in all_proxies:
                    proxy_key = ProxyManager.get_proxy_key(proxy)
                    is_available = proxy in self.balancer.available_proxies
                    with self.balancer.stats_lock:
                        stats = self.balancer.proxy_stats.get(proxy_key)
                        failures = stats.failure_count if stats else 0
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
            self.logger.debug(f"Stats collected: {len(proxy_stats)} proxies monitored")
    def _cleanup_old_proxy_stats(self):
        current_time = time.time()
        keys_to_remove = []
        
        with self.stats_lock:
            for key, stats in self.proxy_stats.items():
                if current_time - stats.get('last_update', 0) > self.cleanup_interval * 2:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self.proxy_stats[key]
            
            if len(self.proxy_stats) > self.max_proxy_stats:
                sorted_keys = sorted(
                    self.proxy_stats.keys(),
                    key=lambda k: self.proxy_stats[k].get('last_update', 0)
                )
                keys_to_remove = sorted_keys[:int(len(self.proxy_stats) * 0.2)]
                for key in keys_to_remove:
                    del self.proxy_stats[key]

    def _periodic_cleanup(self):
        current_time = time.time()
        if current_time - self.last_cleanup_time >= self.cleanup_interval:
            self._cleanup_old_proxy_stats()
            self.last_cleanup_time = current_time
