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
        proxies: List[str],
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

    def _initialize_proxies(self):
        for proxy in self.proxies:
            self.proxy_managers[proxy] = ProxyManager(proxy, self.max_retries)

    def _get_next_proxy(self) -> str:
        with self.lock:
            return self.algorithm.select_proxy(list(self.proxy_managers.keys()))

    def _request(
        self, method: str, url: str, **kwargs: Any
    ) -> requests.Response:  # type: ignore
        retries = 0
        while retries < self.max_retries:
            proxy = self._get_next_proxy()
            try:
                response = self.proxy_managers[proxy].request(
                    method, url, timeout=self.timeout, **kwargs
                )
                if response.status_code == 200:
                    return response
            except Exception as e:
                logger.warning(
                    f"Proxy {proxy} failed with exception {e}, retrying... "
                )
                retries += 1
        raise Exception("Max retries exceeded")

    def get(self, url: str, **kwargs: Any) -> requests.Response:  # type: ignore
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:  # type: ignore
        return self._request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> requests.Response:  # type: ignore
        return self._request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> requests.Response:  # type: ignore
        return self._request("DELETE", url, **kwargs)


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
