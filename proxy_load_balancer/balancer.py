import concurrent.futures
import logging
import random
import threading
import time
import weakref
from typing import Any, Dict, List, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .proxy_selector_algo import AlgorithmFactory, LoadBalancingAlgorithm
from .handler import ProxyHandler
from .server import ProxyBalancerServer
from .utils import ProxyManager


class Balancer:
    def __init__(
        self,
        proxies: List[Dict[str, Any]],
        algorithm: LoadBalancingAlgorithm,
        max_retries: int = 3,
        timeout: int = 5,
    ):
        self.proxies = proxies
        self.algorithm = algorithm
        self.max_retries = max_retries
        self.timeout = timeout
        self.lock = threading.Lock()
        self.proxy_managers: Dict[str, ProxyManager] = {}
        self._initialize_proxies()
        self.logger = logging.getLogger("proxy_balancer")

    def _initialize_proxies(self):
        for proxy in self.proxies:
            key = ProxyManager.get_proxy_key(proxy)
            self.proxy_managers[key] = ProxyManager()

    def _get_next_proxy(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.algorithm.select_proxy(self.proxies)

    def _request(
        self, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        retries = 0
        while retries < self.max_retries:
            proxy = self._get_next_proxy()
            if not proxy:
                raise Exception("No available proxies")
            key = ProxyManager.get_proxy_key(proxy)
            try:
                session = requests.Session()
                session.proxies = {
                    "http": f"socks5://{proxy['host']}:{proxy['port']}",
                    "https": f"socks5://{proxy['host']}:{proxy['port']}"
                }
                response = session.request(
                    method, url, timeout=self.timeout, **kwargs
                )
                if response.status_code == 200:
                    return response
            except Exception as e:
                self.logger.warning(
                    f"Proxy {key} failed with exception {e}, retrying... "
                )
                retries += 1
        raise Exception("Max retries exceeded")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request("DELETE", url, **kwargs)


class ProxyBalancer:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self._available_proxies_set: Set[str] = set()
        self._unavailable_proxies_set: Set[str] = set()
        self.available_proxies: List[Dict[str, Any]] = config.get("proxies", [])
        self.unavailable_proxies: List[Dict[str, Any]] = []
        self._available_proxies_set = set(
            ProxyManager.get_proxy_key(proxy) for proxy in self.available_proxies
        )
        self.sessions: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
        self.failure_counts: Dict[str, int] = {}
        self.request_counts: Dict[str, int] = {}
        self.success_counts: Dict[str, int] = {}
        self.lock = threading.RLock()
        self.server = None
        self.health_thread = None
        self.stop_event = threading.Event()
        self.health_check_pool = None
        self.logger = logging.getLogger("proxy_balancer")
        self._setup_logger()
        algorithm_name = config.get("load_balancing_algorithm", "random")
        try:
            self.load_balancer: LoadBalancingAlgorithm = AlgorithmFactory.create_algorithm(algorithm_name)
        except ValueError as e:
            self.logger.error(f"Algorithm initialization error: {e}")
            self.logger.info("Using default algorithm: random")
            self.load_balancer = AlgorithmFactory.create_algorithm("random")

    def _setup_logger(self):
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            total_requests = sum(self.request_counts.values())
            total_successes = sum(self.success_counts.values())
            total_failures = sum(self.failure_counts.values())
            
            proxy_stats = {}
            all_proxy_keys = set(self.request_counts.keys()) | set(self.success_counts.keys()) | set(self.failure_counts.keys())
            
            for key in all_proxy_keys:
                requests = self.request_counts.get(key, 0)
                successes = self.success_counts.get(key, 0)
                failures = self.failure_counts.get(key, 0)
                # Success rate should be based on successful requests out of total attempted operations
                total_operations = successes + failures
                success_rate = (successes / total_operations * 100) if total_operations > 0 else 0
                
                proxy_stats[key] = {
                    "requests": requests,
                    "successes": successes,
                    "failures": failures,
                    "success_rate": round(success_rate, 2),
                    "status": "available" if key in self._available_proxies_set else "unavailable"
                }
            
            return {
                "total_requests": total_requests,
                "total_successes": total_successes,
                "total_failures": total_failures,
                "overall_success_rate": round((total_successes / (total_successes + total_failures) * 100) if (total_successes + total_failures) > 0 else 0, 2),
                "available_proxies_count": len(self.available_proxies),
                "unavailable_proxies_count": len(self.unavailable_proxies),
                "algorithm": type(self.load_balancer).__name__,
                "proxy_stats": proxy_stats
            }

    def update_proxies(self, new_config: Dict[str, Any]):
        self.config = new_config
        self.available_proxies = new_config.get("proxies", [])
        self._available_proxies_set = set(
            ProxyManager.get_proxy_key(proxy) for proxy in self.available_proxies
        )

    def reload_algorithm(self):
        algorithm_name = self.config.get("load_balancing_algorithm", "random")
        try:
            self.load_balancer = AlgorithmFactory.create_algorithm(algorithm_name)
        except ValueError:
            self.logger.error(f"Unknown algorithm: {algorithm_name}, using random")
            self.load_balancer = AlgorithmFactory.create_algorithm("random")

    def start(self):
        from .handler import ProxyHandler
        from .server import ProxyBalancerServer
        from .monitor import ProxyMonitor
        host = self.config["server"]["host"]
        port = self.config["server"]["port"]
        self.server = ProxyBalancerServer((host, port), ProxyHandler)
        self.server.proxy_balancer = self
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.logger.info(f"ProxyBalancer server started on {host}:{port}")
        self.monitor = ProxyMonitor(self)
        self.monitor.start_monitoring()
        self._run_initial_health_check()

    def _run_initial_health_check(self):
        proxies = self.config.get("proxies", [])
        for proxy in proxies:
            key = ProxyManager.get_proxy_key(proxy)
            self._available_proxies_set.add(key)
            if proxy not in self.available_proxies:
                self.available_proxies.append(proxy)

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if hasattr(self, 'server_thread'):
                self.server_thread.join(timeout=5)
            self.logger.info("ProxyBalancer server stopped")

    def set_config_manager(self, config_manager, on_config_change):
        self._config_manager = config_manager
        self._on_config_change = on_config_change

    def get_next_proxy(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            proxy = self.load_balancer.select_proxy(self.available_proxies)
            if proxy:
                key = ProxyManager.get_proxy_key(proxy)
                self.request_counts[key] = self.request_counts.get(key, 0) + 1
                self.logger.debug(f"Selected proxy {key} (request #{self.request_counts[key]})")
            return proxy

    def get_session(self, proxy: Dict[str, Any]) -> requests.Session:
        key = ProxyManager.get_proxy_key(proxy)
        if key not in self.sessions:
            session = requests.Session()
            session.proxies = {
                "http": f"socks5://{proxy['host']}:{proxy['port']}",
                "https": f"socks5://{proxy['host']}:{proxy['port']}"
            }
            adapter = HTTPAdapter(max_retries=Retry(total=self.config.get("max_retries", 3)))
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            self.sessions[key] = session
        return self.sessions[key]

    def mark_success(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        with self.lock:
            self.failure_counts[key] = 0
            self.success_counts[key] = self.success_counts.get(key, 0) + 1
            self._available_proxies_set.add(key)
            self._unavailable_proxies_set.discard(key)
            
            # Update available_proxies list if proxy is not already there
            if proxy not in self.available_proxies:
                self.available_proxies.append(proxy)
                self.logger.info(f"Proxy {key} restored to available pool")
            
            # Remove from unavailable_proxies list if it's there
            self.unavailable_proxies = [p for p in self.unavailable_proxies if ProxyManager.get_proxy_key(p) != key]
            
            self.logger.debug(f"Proxy {key} success (total: {self.success_counts[key]})")

    def mark_failure(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        with self.lock:
            self.failure_counts[key] = self.failure_counts.get(key, 0) + 1
            self.logger.warning(f"Proxy {key} failed (failure #{self.failure_counts[key]})")
            
            if self.failure_counts[key] >= self.config.get("max_retries", 3):
                self._available_proxies_set.discard(key)
                self._unavailable_proxies_set.add(key)
                
                # Remove from available_proxies list
                self.available_proxies = [p for p in self.available_proxies if ProxyManager.get_proxy_key(p) != key]
                
                # Add to unavailable_proxies list if not already there
                if proxy not in self.unavailable_proxies:
                    self.unavailable_proxies.append(proxy)
                
                self.logger.error(f"Proxy {key} marked as unavailable after {self.failure_counts[key]} failures")

    def print_stats(self) -> None:
        """Print comprehensive proxy statistics"""
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
        """Print compact proxy statistics for periodic updates"""
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
        """Log a summary of statistics"""
        stats = self.get_stats()
        self.logger.info(f"Stats summary - Total requests: {stats['total_requests']}, "
                        f"Success rate: {stats['overall_success_rate']}%, "
                        f"Available proxies: {stats['available_proxies_count']}")
        
        for proxy_key, proxy_stats in stats['proxy_stats'].items():
            if proxy_stats['requests'] > 0:
                self.logger.info(f"Proxy {proxy_key}: {proxy_stats['requests']} requests, "
                               f"{proxy_stats['success_rate']}% success rate, {proxy_stats['status']}")
