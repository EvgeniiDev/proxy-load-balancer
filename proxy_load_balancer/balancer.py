import logging
import threading
from typing import Any, Dict, List, Optional

import requests

from .proxy_selector_algo import LoadBalancingAlgorithm
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
                self.logger.warning(f"Proxy {key} failed with exception {e}, retrying... ")
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




