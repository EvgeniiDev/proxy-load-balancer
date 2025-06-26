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


class ProxyStats:
    def __init__(self):
        self.request_count: int = 0
        self.success_count: int = 0
        self.failure_count: int = 0
        self.session_pool: List[requests.Session] = []
        self.last_used: float = time.time()
        self.last_success: float = 0
        self.last_failure: float = 0
    
    def increment_requests(self):
        self.request_count += 1
        self.last_used = time.time()
    
    def increment_successes(self):
        self.success_count += 1
        self.failure_count = 0  # Сбрасываем счетчик неудач при успехе
        self.last_success = time.time()
        self.last_used = time.time()
    
    def increment_failures(self):
        self.failure_count += 1
        self.last_failure = time.time()
        self.last_used = time.time()
    
    def get_success_rate(self) -> float:
        total_operations = self.success_count + self.failure_count
        if total_operations == 0:
            return 0.0
        return (self.success_count / total_operations) * 100
    
    def add_session(self, session: requests.Session, max_pool_size: int = 5):
        if len(self.session_pool) < max_pool_size:
            self.session_pool.append(session)
            return True
        else:
            session.close()
            return False
    
    def get_session(self) -> Optional[requests.Session]:
        if self.session_pool:
            return self.session_pool.pop()
        return None
    
    def close_all_sessions(self):
        for session in self.session_pool:
            session.close()
        self.session_pool.clear()
    
    def is_stale(self, max_age: float) -> bool:
        return time.time() - self.last_used > max_age


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
    def __init__(self, config: Dict[str, Any], verbose: bool = False) -> None:
        self.config = config
        self.verbose = verbose
        self._available_proxies_set: Set[str] = set()
        self._unavailable_proxies_set: Set[str] = set()
        self.available_proxies: List[Dict[str, Any]] = config.get("proxies", [])
        self.unavailable_proxies: List[Dict[str, Any]] = []
        self._available_proxies_set = set(
            ProxyManager.get_proxy_key(proxy) for proxy in self.available_proxies
        )
        self.sessions: Dict[str, queue.Queue] = {}
        self.proxy_stats: Dict[str, ProxyStats] = {}
        self.stats_lock = threading.Lock()
        self.proxy_selection_lock = threading.Lock()
        self.session_lock = threading.Lock()
        self.max_stats_entries = 1000
        self.max_session_pool_size = 5
        self.cleanup_interval = 300
        self.last_cleanup_time = time.time()
        self.max_stats_entries = 1000
        self.cleanup_interval = 300
        self.last_cleanup_time = time.time()
        self.server = None
        self.health_thread = None
        self.stats_thread = None
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
            total_requests = sum(stats.request_count for stats in self.proxy_stats.values())
            total_successes = sum(stats.success_count for stats in self.proxy_stats.values())
            total_failures = sum(stats.failure_count for stats in self.proxy_stats.values())
            
            proxy_stats = {}
            
            for key, stats in self.proxy_stats.items():
                proxy_stats[key] = {
                    "requests": stats.request_count,
                    "successes": stats.success_count,
                    "failures": stats.failure_count,
                    "success_rate": round(stats.get_success_rate(), 2),
                    "status": "available" if key in self._available_proxies_set else "unavailable",
                    "last_used": stats.last_used,
                    "sessions_pooled": len(stats.session_pool)
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
        new_proxies = new_config.get("proxies", [])
        new_proxy_keys = set(ProxyManager.get_proxy_key(proxy) for proxy in new_proxies)
        
        with self.proxy_selection_lock:
            self.available_proxies = new_proxies
            self._available_proxies_set = new_proxy_keys
            self.unavailable_proxies = []
            self._unavailable_proxies_set = set()
        
        self._cleanup_old_proxy_data(new_proxy_keys)

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
        self._start_stats_monitoring()

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
                        stats = self._get_or_create_proxy_stats(key)
                        stats.failure_count = 0
                    
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
        self._periodic_cleanup()
        
        with self.proxy_selection_lock:
            proxy = self.load_balancer.select_proxy(self.available_proxies)
            if proxy:
                key = ProxyManager.get_proxy_key(proxy)
                with self.stats_lock:
                    stats = self._get_or_create_proxy_stats(key)
                    stats.increment_requests()
                self.logger.debug(f"Selected proxy {key} (request #{stats.request_count})")
            return proxy

    def get_session(self, proxy: Dict[str, Any]) -> requests.Session:
        key = ProxyManager.get_proxy_key(proxy)
        
        with self.session_lock:
            stats = self._get_or_create_proxy_stats(key)
            session = stats.get_session()
            
            if session:
                return session
            
            # Создаем новую сессию
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
            stats = self._get_or_create_proxy_stats(key)
            stats.add_session(session, self.max_session_pool_size)

    def mark_success(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.increment_successes()
            
        with self.proxy_selection_lock:
            self._available_proxies_set.add(key)
            self._unavailable_proxies_set.discard(key)
            
            if proxy not in self.available_proxies:
                self.available_proxies.append(proxy)
                self.logger.info(f"Proxy {key} restored to available pool")
            
            self.unavailable_proxies = [p for p in self.unavailable_proxies if ProxyManager.get_proxy_key(p) != key]
            
        self.logger.debug(f"Proxy {key} success (total: {stats.success_count})")

    def mark_failure(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.increment_failures()
            failure_count = stats.failure_count
        
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
        if not self.verbose:
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
    
    def _start_stats_monitoring(self):
        if not self.verbose:
            return
            
        self.stats_thread = threading.Thread(target=self._stats_monitoring_loop, daemon=True)
        self.stats_thread.start()
        self.logger.info("Statistics monitoring started in verbose mode")
    
    def _stats_monitoring_loop(self):
        stats_interval = self.config.get("stats_interval", 30)  
        
        while not self.health_check_stop_event.is_set():
            self.health_check_stop_event.wait(stats_interval)
            if not self.health_check_stop_event.is_set():
                self.print_compact_stats()

    def _cleanup_old_proxy_data(self, current_proxy_keys: Set[str]):
        with self.stats_lock:
            orphaned_keys = set(self.proxy_stats.keys()) - current_proxy_keys
            
            for key in orphaned_keys:
                if key in self.proxy_stats:
                    self.proxy_stats[key].close_all_sessions()
                    del self.proxy_stats[key]

    def _periodic_cleanup(self):
        current_time = time.time()
        if current_time - self.last_cleanup_time >= self.cleanup_interval:
            self._cleanup_stats()
            self._cleanup_session_pools()
            self.last_cleanup_time = current_time

    def _cleanup_stats(self):
        with self.stats_lock:
            current_proxy_keys = self._available_proxies_set | self._unavailable_proxies_set
            orphaned_keys = set(self.proxy_stats.keys()) - current_proxy_keys
            
            for key in orphaned_keys:
                if key in self.proxy_stats:
                    self.proxy_stats[key].close_all_sessions()
                    del self.proxy_stats[key]
            
            self._enforce_stats_limits()

    def _cleanup_session_pools(self):
        # Эта функция теперь не нужна, так как сессии хранятся в ProxyStats
        pass

    def _enforce_stats_limits(self):
        if len(self.proxy_stats) > self.max_stats_entries:
            # Удаляем 20% самых старых записей по времени последнего использования
            sorted_keys = sorted(
                self.proxy_stats.keys(),
                key=lambda k: self.proxy_stats[k].last_used
            )
            keys_to_remove = sorted_keys[:int(self.max_stats_entries * 0.2)]
            for key in keys_to_remove:
                self.proxy_stats[key].close_all_sessions()
                del self.proxy_stats[key]
        
        # Если все еще превышаем лимит, удаляем больше записей
        while len(self.proxy_stats) > self.max_stats_entries:
            oldest_key = min(self.proxy_stats.keys(), key=lambda k: self.proxy_stats[k].last_used)
            self.proxy_stats[oldest_key].close_all_sessions()
            del self.proxy_stats[oldest_key]

    def _get_or_create_proxy_stats(self, key: str) -> ProxyStats:
        """Получить существующую или создать новую статистику для прокси"""
        if key not in self.proxy_stats:
            self.proxy_stats[key] = ProxyStats()
        return self.proxy_stats[key]




