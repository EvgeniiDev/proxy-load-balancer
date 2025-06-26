import time
import logging
import threading
import collections
from typing import Dict, List, Any, Deque, TYPE_CHECKING
from .utils import ProxyManager

if TYPE_CHECKING:
    from .proxy_balancer import ProxyBalancer


class StatsReporter:
    def __init__(self, proxy_balancer: 'ProxyBalancer', max_history: int = 100):
        self.proxy_balancer = proxy_balancer
        self.logger = logging.getLogger("stats_reporter")
        
        # Monitoring functionality
        self.is_monitoring = False
        self.monitor_thread = None
        self.stop_event = threading.Event()
        self.stats_history: Deque[Dict[str, Any]] = collections.deque(maxlen=max_history)
        self.proxy_stats: Dict[str, Dict[str, Any]] = {}
        self.stats_lock = threading.RLock()
        self.max_proxy_stats = 1000
        self.cleanup_interval = 300
        self.last_cleanup_time = time.time()
        
        self._setup_logger()
    
    def _setup_logger(self):
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def get_stats(self) -> Dict[str, Any]:
        with self.proxy_balancer.stats_lock:
            total_requests = sum(stats.request_count for stats in self.proxy_balancer.proxy_stats.values())
            total_successes = sum(stats.success_count for stats in self.proxy_balancer.proxy_stats.values())
            total_failures = sum(stats.failure_count for stats in self.proxy_balancer.proxy_stats.values())
            
            proxy_stats = {}
            
            for key, stats in self.proxy_balancer.proxy_stats.items():
                proxy_stats[key] = {
                    "requests": stats.request_count,
                    "successes": stats.success_count,
                    "failures": stats.failure_count,
                    "success_rate": round(stats.get_success_rate(), 2),
                    "status": "available" if any(ProxyManager.get_proxy_key(p) == key for p in self.proxy_balancer.available_proxies) else "unavailable",
                    "sessions_pooled": len(stats.session_pool)
                }
            
            return {
                "total_requests": total_requests,
                "total_successes": total_successes,
                "total_failures": total_failures,
                "overall_success_rate": round((total_successes / (total_successes + total_failures) * 100) if (total_successes + total_failures) > 0 else 0, 2),
                "available_proxies_count": len(self.proxy_balancer.available_proxies),
                "unavailable_proxies_count": len(self.proxy_balancer.unavailable_proxies),
                "algorithm": type(self.proxy_balancer.load_balancer).__name__,
                "proxy_stats": proxy_stats
            }

    def print_stats(self) -> None:
        stats = self.get_stats()
        
        print("\n" + "="*60)
        print("PROXY LOAD BALANCER STATISTICS")
        print("="*60)
        print(f"Algorithm: {stats['algorithm']}")
        print(f"Total Requests: {stats['total_requests']}")
        print(f"Total Successes: {stats['total_successes']}")
        print(f"Total Failures: {stats['total_failures']}")
        print(f"Overall Success Rate: {stats['overall_success_rate']}%")
        print(f"Available Proxies: {stats['available_proxies_count']}")
        print(f"Unavailable Proxies: {stats['unavailable_proxies_count']}")
        
        print("\nPER-PROXY STATISTICS:")
        print("-" * 60)
        print(f"{'Proxy':<20} {'Requests':<10} {'Success':<10} {'Failures':<10} {'Rate':<8} {'Status':<12}")
        print("-" * 60)
        
        for proxy_key, proxy_stats in stats['proxy_stats'].items():
            print(f"{proxy_key:<20} {proxy_stats['requests']:<10} {proxy_stats['successes']:<10} "
                  f"{proxy_stats['failures']:<10} {proxy_stats['success_rate']:<7}% {proxy_stats['status']:<12}")
        
        print("="*60)

    def print_compact_stats(self) -> None:
        if not self.proxy_balancer.verbose:
            return
            
        stats = self.get_stats()
        
        print(f"[{time.strftime('%H:%M:%S')}] Stats: {stats['total_requests']} reqs, "
              f"{stats['overall_success_rate']}% success, "
              f"{stats['available_proxies_count']}/{stats['available_proxies_count'] + stats['unavailable_proxies_count']} proxies up | ", end="")
        
        proxy_summaries = []
        for proxy_key, proxy_stats in stats['proxy_stats'].items():
            if proxy_stats['requests'] > 0:
                status_symbol = "✓" if proxy_stats['status'] == "available" else "✗"
                proxy_summaries.append(f"{proxy_key}({proxy_stats['requests']}r/{proxy_stats['success_rate']}%{status_symbol})")
        
        print(" | ".join(proxy_summaries))

    def log_stats_summary(self) -> None:
        stats = self.get_stats()
        self.logger.info(f"Stats Summary - Requests: {stats['total_requests']}, "
                        f"Success Rate: {stats['overall_success_rate']}%, "
                        f"Available Proxies: {stats['available_proxies_count']}/{stats['available_proxies_count'] + stats['unavailable_proxies_count']}")

    # Monitoring functionality
    def start_monitoring(self):
        if self.is_monitoring:
            return
        self.is_monitoring = True
        self.stop_event.clear()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.logger.info("Stats monitoring started")

    def stop_monitoring(self):
        if not self.is_monitoring:
            return
        self.is_monitoring = False
        self.stop_event.set()
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        self.logger.info("Stats monitoring stopped")

    def _monitor_loop(self):
        interval = 10
        stats_interval = 60
        console_stats_interval = 30
        
        try:
            interval = self.proxy_balancer.config.get("monitoring_interval", 10)
            stats_interval = self.proxy_balancer.config.get("stats_log_interval", 60)
            console_stats_interval = self.proxy_balancer.config.get("console_stats_interval", 30)
        except (AttributeError, KeyError):
            pass
        
        last_stats_time = 0
        last_console_stats_time = 0
        
        while not self.stop_event.wait(interval):
            try:
                self._periodic_cleanup()
                self._collect_stats()
                
                current_time = time.time()
                
                if current_time - last_console_stats_time >= console_stats_interval:
                    compact_mode = self.proxy_balancer.config.get("compact_console_stats", False)
                    if compact_mode:
                        self.print_compact_stats()
                    else:
                        print("\n" + "="*80)
                        print("PERIODIC PROXY STATISTICS UPDATE")
                        print("="*80)
                        self.print_stats()
                    last_console_stats_time = current_time
                
                if current_time - last_stats_time >= stats_interval:
                    self.log_stats_summary()
                    last_stats_time = current_time
                    
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {str(e)}")

    def _collect_stats(self):
        with self.stats_lock:
            balancer_stats = self.get_stats()
            timestamp = time.time()
            proxy_stats = []
            
            with self.proxy_balancer.proxy_selection_lock:
                all_proxies = self.proxy_balancer.available_proxies + self.proxy_balancer.unavailable_proxies
                for proxy in all_proxies:
                    proxy_key = ProxyManager.get_proxy_key(proxy)
                    is_available = proxy in self.proxy_balancer.available_proxies
                    
                    with self.proxy_balancer.stats_lock:
                        stats = self.proxy_balancer.proxy_stats.get(proxy_key)
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

    def get_stats_history(self) -> List[Dict[str, Any]]:
        with self.stats_lock:
            return list(self.stats_history)