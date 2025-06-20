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
        self.available_proxies: List[Dict[str, Any]] = []
        self.unavailable_proxies: List[Dict[str, Any]] = []
        self.sessions: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
        self.failure_counts: Dict[str, int] = {}
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
        return {}

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
            return self.load_balancer.select_proxy(self.available_proxies)

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
        self.failure_counts[key] = 0
        self._available_proxies_set.add(key)
        self._unavailable_proxies_set.discard(key)

    def mark_failure(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        self.failure_counts[key] = self.failure_counts.get(key, 0) + 1
        if self.failure_counts[key] >= self.config.get("max_retries", 3):
            self._available_proxies_set.discard(key)
            self._unavailable_proxies_set.add(key)
