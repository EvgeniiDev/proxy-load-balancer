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


class ProxyBalancer:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        # Use sets for O(1) membership testing and removal
        self._available_proxies_set = set()
        self._unavailable_proxies_set = set()
        # Keep lists for algorithms that need ordered data
        self.available_proxies = []
        self.unavailable_proxies = []
        self.sessions = weakref.WeakValueDictionary()
        self.failure_counts = {}
        self.lock = threading.RLock()
        self.server = None
        self.health_thread = None
        self.stop_event = threading.Event()
        self.health_check_pool = None
        self.logger = self._setup_logger()

        algorithm_name = config.get("load_balancing_algorithm", "random")
        try:
            self.load_balancer: LoadBalancingAlgorithm = AlgorithmFactory.create_algorithm(algorithm_name)
        except ValueError as e:
            self.logger.error(f"Algorithm initialization error: {e}")
            self.logger.info("Using default algorithm: random")
            self.load_balancer = AlgorithmFactory.create_algorithm("random")
            
    def _setup_logger(self):
        logger = logging.getLogger("proxy_balancer")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def start(self) -> None:
        self._init_proxies()
        self._start_server()
        self._start_health_checker()

    def stop(self) -> None:
        self.stop_event.set()

        if self.health_check_pool:
            self.health_check_pool.shutdown(wait=False)
            
        if self.health_thread:
            self.health_thread.join(timeout=5)

        if self.server:
            self.server.shutdown()
            self.server.server_close()

        for session in list(self.sessions.values()):
            try:
                session.close()
            except Exception:
                pass

    def update_proxies(self, new_config: Dict[str, Any]) -> None:
        # First collect information about proxies outside the lock
        old_proxies_dict = {}
        new_proxies_dict = {}
        
        # Use sets for more efficient operations
        with self.lock:
            old_proxies_dict = {ProxyManager.get_proxy_key(p): p for p in (
                self.available_proxies + self.unavailable_proxies)}
        
        new_proxies_dict = {ProxyManager.get_proxy_key({"host": p["host"], "port": p["port"]}): {"host": p["host"], "port": p["port"]}
                        for p in new_config["proxies"]}
        
        # Calculate differences
        proxies_to_remove = set(old_proxies_dict.keys()) - set(new_proxies_dict.keys())
        proxies_to_add = set(new_proxies_dict.keys()) - set(old_proxies_dict.keys())
        
        # Test new proxies outside the lock
        proxy_test_results = {}
        for proxy_key in proxies_to_add:
            proxy = new_proxies_dict[proxy_key]
            session = self._create_session(proxy)
            is_working = self._test_proxy(session)
            proxy_test_results[proxy_key] = (proxy, session, is_working)
        
        # Now update data structures with minimal lock time
        with self.lock:
            # Update config
            self.config.update(new_config)
            
            # Remove old proxies
            for proxy_key in proxies_to_remove:
                proxy = old_proxies_dict[proxy_key]

                if proxy in self.available_proxies:
                    self.available_proxies.remove(proxy)
                if proxy in self.unavailable_proxies:
                    self.unavailable_proxies.remove(proxy)

                if proxy_key in self.sessions:
                    try:
                        self.sessions[proxy_key].close()
                    except Exception:
                        pass
                    del self.sessions[proxy_key]

                if proxy_key in self.failure_counts:
                    del self.failure_counts[proxy_key]

                self.logger.info(f"Removed proxy: {proxy['host']}:{proxy['port']}")
            
            # Add new proxies with already tested results
            for proxy_key, (proxy, session, is_working) in proxy_test_results.items():
                self.sessions[proxy_key] = session
                self.failure_counts[proxy_key] = 0

                if is_working:
                    self.available_proxies.append(proxy)
                    self.logger.info(f"Added available proxy: {proxy['host']}:{proxy['port']}")
                else:
                    self.unavailable_proxies.append(proxy)
                    self.logger.info(f"Added unavailable proxy: {proxy['host']}:{proxy['port']}")
            
        # Log summary outside the lock
        self.logger.info(
            f"Proxy update completed. Available: {len(self.available_proxies)}, Unavailable: {len(self.unavailable_proxies)}")

    def reload_algorithm(self) -> None:
        algorithm_name = self.config.get("load_balancing_algorithm", "random")
        try:
            new_algorithm = AlgorithmFactory.create_algorithm(algorithm_name)
            with self.lock:
                self.load_balancer = new_algorithm
            print(f"Load balancing algorithm updated to: {algorithm_name}")
        except ValueError as e:
            print(f"Ошибка при обновлении алгоритма: {e}")
            print("Алгоритм балансировки остается без изменений")

    def _init_proxies(self):
        # Test proxies in parallel
        futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            for proxy_config in self.config["proxies"]:
                proxy = {"host": proxy_config["host"], "port": proxy_config["port"]}
                session = self._create_session(proxy)
                self.sessions[ProxyManager.get_proxy_key(proxy)] = session
                futures.append((proxy, executor.submit(self._test_proxy, session)))
        
        # Process results
        for proxy, future in futures:
            try:
                is_working = future.result()
                proxy_key_tuple = tuple(sorted(proxy.items()))
                if is_working:
                    self.available_proxies.append(proxy)
                    self._available_proxies_set.add(proxy_key_tuple)
                else:
                    self.unavailable_proxies.append(proxy)
                    self._unavailable_proxies_set.add(proxy_key_tuple)
            except Exception as e:
                self.logger.error(f"Error initializing proxy: {e}")
                # If error, consider proxy as unavailable
                self.unavailable_proxies.append(proxy)
                self._unavailable_proxies_set.add(tuple(sorted(proxy.items())))

    def _create_session(self, proxy: Dict[str, Any]) -> requests.Session:
        session = requests.Session()
        
        # Configure connection pooling
        connection_timeout = self.config.get("connection_timeout", 10)
        pool_connections = self.config.get("pool_connections", 10)
        pool_maxsize = self.config.get("pool_maxsize", 20)
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        adapter = HTTPAdapter(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            max_retries=retry_strategy
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        session.proxies = {
            "http": f"socks5://{proxy['host']}:{proxy['port']}",
            "https": f"socks5://{proxy['host']}:{proxy['port']}",
        }
        return session

    def _test_proxy(self, session: requests.Session) -> bool:
        test_urls: List[str] = ["http://httpbin.org/ip", "http://icanhazip.com"]
        timeout = self.config.get("health_check_timeout", 10)
        for url in test_urls:
            try:
                response = session.get(url, timeout=timeout)
                if response.status_code == 200:
                    return True
            except (requests.RequestException, ConnectionError) as e:
                self.logger.debug(f"Proxy test failed: {str(e)}")
                continue
            except Exception as e:
                self.logger.debug(f"Unexpected error testing proxy: {str(e)}")
                continue
        return False

    def _start_server(self) -> None:
        server_config = self.config["server"]
        self.server = ProxyBalancerServer((server_config["host"], server_config["port"]), ProxyHandler)
        self.server.proxy_balancer = self

        server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        server_thread.start()

    def _start_health_checker(self) -> None:
        max_workers = min(20, len(self.available_proxies) + len(self.unavailable_proxies))
        max_workers = max(4, max_workers)  # At least 4 workers
        self.health_check_pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self.health_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.health_thread.start()

    def _health_check_loop(self) -> None:
        while not self.stop_event.is_set():
            self._check_unavailable_proxies()
            self._check_available_proxies()
            self.stop_event.wait(self.config["health_check_interval"])

    def _check_unavailable_proxies(self) -> None:
        if not self.unavailable_proxies:
            return

        # Check all unavailable proxies in parallel 
        proxies_to_check = self.unavailable_proxies.copy()
        futures = []
        
        for proxy in proxies_to_check:
            if self.stop_event.is_set():
                break
                
            proxy_key = ProxyManager.get_proxy_key(proxy)
            if proxy_key in self.sessions:
                session = self.sessions[proxy_key]
                futures.append((proxy, self.health_check_pool.submit(self._test_proxy, session)))
        
        for proxy, future in futures:
            if self.stop_event.is_set():
                break
                
            try:
                is_healthy = future.result()
                if is_healthy:
                    with self.lock:
                        if proxy in self.unavailable_proxies:
                            self.unavailable_proxies.remove(proxy)
                            self.available_proxies.append(proxy)
                            self.failure_counts[ProxyManager.get_proxy_key(proxy)] = 0
                            self.logger.info(f"Proxy {proxy['host']}:{proxy['port']} is back online")
            except Exception as e:
                self.logger.warning(f"Error checking proxy health: {str(e)}")

    def _check_available_proxies(self) -> None:
        if not self.available_proxies:
            return
            
        # Sample a subset of proxies to check
        sample_size = min(max(len(self.available_proxies) // 4, 1), 5)
        proxies_to_check = random.sample(self.available_proxies, sample_size)
        
        futures = []
        for proxy in proxies_to_check:
            proxy_key = ProxyManager.get_proxy_key(proxy)
            if proxy_key in self.sessions:
                session = self.sessions[proxy_key]
                futures.append((proxy, self.health_check_pool.submit(self._test_proxy, session)))
        
        for proxy, future in futures:
            try:
                is_healthy = future.result()
                if not is_healthy:
                    with self.lock:
                        if proxy in self.available_proxies:
                            self.available_proxies.remove(proxy)
                            self.unavailable_proxies.append(proxy)
                            self.logger.info(f"Proxy {proxy['host']}:{proxy['port']} is down")
            except Exception as e:
                self.logger.warning(f"Error checking proxy health: {str(e)}")

    def get_next_proxy(self) -> Optional[Dict[str, Any]]:
        with self.lock:
            if not self.available_proxies:
                return None
            return self.load_balancer.select_proxy(self.available_proxies)

    def get_session(self, proxy: Dict[str, Any]) -> Optional[requests.Session]:
        return self.sessions.get(ProxyManager.get_proxy_key(proxy))

    def mark_success(self, proxy: Dict[str, Any]) -> None:
        key = ProxyManager.get_proxy_key(proxy)
        if key in self.failure_counts:
            self.failure_counts[key] = 0

    def mark_failure(self, proxy: Dict[str, Any]) -> None:
        key = ProxyManager.get_proxy_key(proxy)
        self.failure_counts[key] = self.failure_counts.get(key, 0) + 1

        if self.failure_counts[key] >= self.config["max_retries"]:
            with self.lock:
                proxy_key_tuple = tuple(sorted(proxy.items()))
                if proxy_key_tuple in self._available_proxies_set:
                    self._available_proxies_set.remove(proxy_key_tuple)
                    self.available_proxies.remove(proxy)
                    self.unavailable_proxies.append(proxy)
                    self._unavailable_proxies_set.add(proxy_key_tuple)

    def get_stats(self) -> Dict[str, int]:
        with self.lock:
            return {
                "available": len(self.available_proxies),
                "unavailable": len(self.unavailable_proxies),
                "total": len(self.available_proxies) + len(self.unavailable_proxies),
            }
