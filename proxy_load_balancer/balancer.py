import concurrent.futures
import logging
import random
import threading
import time
import weakref
from typing import Any, Dict, List, Optional, Set
from threading import RLock
from collections import deque
import queue
import socket

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import socks

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
        self.sessions: Dict[str, queue.Queue] = {}
        self.session_pools: Dict[str, List[requests.Session]] = {}
        self.failure_counts: Dict[str, int] = {}
        self.request_counts: Dict[str, int] = {}
        self.success_counts: Dict[str, int] = {}
        self.stats_lock = threading.Lock()
        self.proxy_selection_lock = threading.Lock()
        self.session_lock = threading.Lock()
        self.server = None
        self.health_thread = None
        self.stop_event = threading.Event()
        self.health_check_pool = None
        self.health_check_stop_event = threading.Event()
        self.health_check_thread = None
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
        with self.stats_lock:
            total_requests = sum(self.request_counts.values())
            total_successes = sum(self.success_counts.values())
            total_failures = sum(self.failure_counts.values())
            
            proxy_stats = {}
            all_proxy_keys = set(self.request_counts.keys()) | set(self.success_counts.keys()) | set(self.failure_counts.keys())
            
            for key in all_proxy_keys:
                requests = self.request_counts.get(key, 0)
                successes = self.success_counts.get(key, 0)
                failures = self.failure_counts.get(key, 0)
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
        self._start_health_check_loop()

    def _start_health_check_loop(self):
        self.health_check_stop_event.clear()
        self.health_check_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.health_check_thread.start()
        self.logger.info("Health check loop started")

    def _health_check_loop(self):
        health_check_interval = self.config.get("health_check_interval", 30)
        unavailable_check_interval = max(5, health_check_interval // 6)
        
        last_full_check = 0
        
        while not self.health_check_stop_event.is_set():
            current_time = time.time()
            
            # Быстрая проверка недоступных прокси
            if len(self.unavailable_proxies) > 0:
                self._check_unavailable_proxies()
            
            # Полная проверка всех прокси с основным интервалом
            if current_time - last_full_check >= health_check_interval:
                self._check_all_proxies()
                last_full_check = current_time
            
            # Ждем меньший интервал для проверки недоступных прокси
            self.health_check_stop_event.wait(unavailable_check_interval)

    def _check_unavailable_proxies(self):
        if not self.unavailable_proxies:
            return
            
        proxies_to_check = list(self.unavailable_proxies)
        self.logger.debug(f"Quick health check for {len(proxies_to_check)} unavailable proxies")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(proxies_to_check), 10)) as executor:
            future_to_proxy = {
                executor.submit(self._test_proxy_health, proxy): proxy 
                for proxy in proxies_to_check
            }
            
            for future in concurrent.futures.as_completed(future_to_proxy, timeout=15):
                proxy = future_to_proxy[future]
                try:
                    is_healthy = future.result()
                    if is_healthy:
                        self._restore_proxy(proxy)
                except Exception as e:
                    self.logger.debug(f"Health check failed for {ProxyManager.get_proxy_key(proxy)}: {e}")

    def _check_all_proxies(self):
        all_proxies = self.available_proxies + self.unavailable_proxies
        if not all_proxies:
            return
            
        self.logger.debug(f"Full health check for {len(all_proxies)} proxies")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(all_proxies), 20)) as executor:
            future_to_proxy = {
                executor.submit(self._test_proxy_health, proxy): proxy 
                for proxy in all_proxies
            }
            
            for future in concurrent.futures.as_completed(future_to_proxy, timeout=30):
                proxy = future_to_proxy[future]
                proxy_key = ProxyManager.get_proxy_key(proxy)
                try:
                    is_healthy = future.result()
                    if is_healthy:
                        if proxy in self.unavailable_proxies:
                            self._restore_proxy(proxy)
                    else:
                        if proxy in self.available_proxies:
                            self.logger.warning(f"Proxy {proxy_key} failed health check")
                            self._mark_proxy_unhealthy(proxy)
                except Exception as e:
                    self.logger.debug(f"Health check failed for {proxy_key}: {e}")
                    if proxy in self.available_proxies:
                        self._mark_proxy_unhealthy(proxy)

    def _test_proxy_health(self, proxy: Dict[str, Any]) -> bool:
        try:
            # Создаем тестовое соединение через SOCKS5
            sock = socks.socksocket()
            sock.set_proxy(
                proxy_type=socks.SOCKS5,
                addr=proxy["host"],
                port=proxy["port"],
                username=proxy.get("username"),
                password=proxy.get("password"),
            )
            sock.settimeout(5)
            
            # Пробуем подключиться к тестовому серверу
            test_hosts = [
                ("httpbin.org", 80),
                ("google.com", 80),
                ("cloudflare.com", 80)
            ]
            
            for host, port in test_hosts:
                try:
                    sock.connect((host, port))
                    sock.close()
                    return True
                except:
                    continue
            
            sock.close()
            return False
            
        except Exception:
            return False

    def _restore_proxy(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        
        with self.proxy_selection_lock:
            if proxy in self.unavailable_proxies:
                self.unavailable_proxies.remove(proxy)
                self._unavailable_proxies_set.discard(key)
                
                if proxy not in self.available_proxies:
                    self.available_proxies.append(proxy)
                    self._available_proxies_set.add(key)
                    
                    with self.stats_lock:
                        self.failure_counts[key] = 0
                    
                    self.logger.info(f"Proxy {key} restored to available pool via health check")

    def _mark_proxy_unhealthy(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        
        with self.proxy_selection_lock:
            if proxy in self.available_proxies:
                self.available_proxies.remove(proxy)
                self._available_proxies_set.discard(key)
                
                if proxy not in self.unavailable_proxies:
                    self.unavailable_proxies.append(proxy)
                    self._unavailable_proxies_set.add(key)
                    
                    self.logger.warning(f"Proxy {key} marked as unhealthy via health check")

    def _run_initial_health_check(self):
        proxies = self.config.get("proxies", [])
        for proxy in proxies:
            key = ProxyManager.get_proxy_key(proxy)
            self._available_proxies_set.add(key)
            if proxy not in self.available_proxies:
                self.available_proxies.append(proxy)

    def stop(self):
        self.health_check_stop_event.set()
        if self.health_check_thread:
            self.health_check_thread.join(timeout=5)
            
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if hasattr(self, 'server_thread'):
                self.server_thread.join(timeout=5)
            self.logger.info("ProxyBalancer server stopped")

        if hasattr(self, 'monitor') and self.monitor:
            self.monitor.stop_monitoring()

    def set_config_manager(self, config_manager, on_config_change):
        self._config_manager = config_manager
        self._on_config_change = on_config_change

    def get_next_proxy(self) -> Optional[Dict[str, Any]]:
        with self.proxy_selection_lock:
            proxy = self.load_balancer.select_proxy(self.available_proxies)
            if proxy:
                key = ProxyManager.get_proxy_key(proxy)
                with self.stats_lock:
                    self.request_counts[key] = self.request_counts.get(key, 0) + 1
                self.logger.debug(f"Selected proxy {key} (request #{self.request_counts[key]})")
            return proxy

    def get_session(self, proxy: Dict[str, Any]) -> requests.Session:
        key = ProxyManager.get_proxy_key(proxy)
        
        with self.session_lock:
            if key not in self.session_pools:
                self.session_pools[key] = []
            
            if self.session_pools[key]:
                return self.session_pools[key].pop()
            
            session = requests.Session()
            session.proxies = {
                "http": f"socks5://{proxy['host']}:{proxy['port']}",
                "https": f"socks5://{proxy['host']}:{proxy['port']}"
            }
            
            session.headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })
            
            adapter = HTTPAdapter(
                max_retries=Retry(total=self.config.get("max_retries", 3)),
                pool_connections=10,
                pool_maxsize=20
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            return session

    def return_session(self, proxy: Dict[str, Any], session: requests.Session):
        key = ProxyManager.get_proxy_key(proxy)
        with self.session_lock:
            if key not in self.session_pools:
                self.session_pools[key] = []
            if len(self.session_pools[key]) < 5:
                self.session_pools[key].append(session)
            else:
                session.close()

    def mark_success(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        with self.stats_lock:
            self.failure_counts[key] = 0
            self.success_counts[key] = self.success_counts.get(key, 0) + 1
            
        with self.proxy_selection_lock:
            self._available_proxies_set.add(key)
            self._unavailable_proxies_set.discard(key)
            
            if proxy not in self.available_proxies:
                self.available_proxies.append(proxy)
                self.logger.info(f"Proxy {key} restored to available pool")
            
            self.unavailable_proxies = [p for p in self.unavailable_proxies if ProxyManager.get_proxy_key(p) != key]
            
        self.logger.debug(f"Proxy {key} success (total: {self.success_counts[key]})")

    def mark_failure(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        with self.stats_lock:
            self.failure_counts[key] = self.failure_counts.get(key, 0) + 1
            failure_count = self.failure_counts[key]
        
        self.logger.warning(f"Proxy {key} failed (failure #{failure_count})")
        
        if failure_count >= self.config.get("max_retries", 3):
            with self.proxy_selection_lock:
                self._available_proxies_set.discard(key)
                self._unavailable_proxies_set.add(key)
                
                self.available_proxies = [p for p in self.available_proxies if ProxyManager.get_proxy_key(p) != key]
                
                if proxy not in self.unavailable_proxies:
                    self.unavailable_proxies.append(proxy)
                
                self.logger.error(f"Proxy {key} marked as unavailable after {failure_count} failures")

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


