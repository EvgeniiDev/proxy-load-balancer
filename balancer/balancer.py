import random
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from .proxy_selector_algo import AlgorithmFactory, LoadBalancingAlgorithm
from .handler import ProxyHandler
from .server import ProxyBalancerServer
from .utils import ProxyManager


class ProxyBalancer:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.available_proxies = []
        self.unavailable_proxies = []
        self.sessions = {}
        self.failure_counts = {}
        self.lock = threading.RLock()
        self.server = None
        self.health_thread = None
        self.stop_event = threading.Event()

        # Инициализируем алгоритм балансировки
        algorithm_name = config.get("load_balancing_algorithm", "random")
        try:
            self.load_balancer: LoadBalancingAlgorithm = AlgorithmFactory.create_algorithm(
                algorithm_name)
        except ValueError as e:
            print(f"Ошибка инициализации алгоритма: {e}")
            print("Используется алгоритм по умолчанию: random")
            self.load_balancer = AlgorithmFactory.create_algorithm("random")

    def start(self) -> None:
        self._init_proxies()
        self._start_server()
        self._start_health_checker()

    def stop(self) -> None:
        self.stop_event.set()

        if self.health_thread:
            self.health_thread.join(timeout=5)

        if self.server:
            self.server.shutdown()
            self.server.server_close()

        for session in self.sessions.values():
            try:
                session.close()
            except Exception:
                pass

    def _init_proxies(self):
        for proxy_config in self.config["proxies"]:
            proxy = {"host": proxy_config["host"],
                     "port": proxy_config["port"]}
            session = self._create_session(proxy)
            self.sessions[ProxyManager.get_proxy_key(proxy)] = session

            if self._test_proxy(session):
                self.available_proxies.append(proxy)
            else:
                self.unavailable_proxies.append(proxy)

    def _create_session(self, proxy: Dict[str, Any]) -> requests.Session:
        session: requests.Session = requests.Session()
        session.proxies = {
            "http": f"socks5://{proxy['host']}:{proxy['port']}",
            "https": f"socks5://{proxy['host']}:{proxy['port']}",
        }
        return session

    def _test_proxy(self, session: requests.Session) -> bool:
        test_urls: List[str] = [
            "http://httpbin.org/ip", "http://icanhazip.com"]
        for url in test_urls:
            try:
                response = session.get(url, timeout=10)
                if response.status_code == 200:
                    return True
            except Exception:
                continue
        return False

    def _start_server(self) -> None:
        server_config = self.config["server"]
        self.server = ProxyBalancerServer(
            (server_config["host"], server_config["port"]), ProxyHandler
        )
        self.server.proxy_balancer = self

        server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        server_thread.start()

    def _start_health_checker(self) -> None:
        self.health_thread = threading.Thread(
            target=self._health_check_loop, daemon=True
        )
        self.health_thread.start()

    def _health_check_loop(self) -> None:
        while not self.stop_event.is_set():
            self._check_unavailable_proxies()
            self._check_available_proxies()
            self.stop_event.wait(self.config["health_check_interval"])

    def _check_unavailable_proxies(self) -> None:
        if not self.unavailable_proxies:
            return

        for proxy in self.unavailable_proxies[:2]:
            if self.stop_event.is_set():
                break

            session = self.sessions[ProxyManager.get_proxy_key(proxy)]
            if self._test_proxy(session):
                with self.lock:
                    if proxy in self.unavailable_proxies:
                        self.unavailable_proxies.remove(proxy)
                        self.available_proxies.append(proxy)
                        self.failure_counts[ProxyManager.get_proxy_key(
                            proxy)] = 0

    def _check_available_proxies(self) -> None:
        if not self.available_proxies:
            return

        proxy = random.choice(self.available_proxies)
        session = self.sessions[ProxyManager.get_proxy_key(proxy)]

        if not self._test_proxy(session):
            with self.lock:
                if proxy in self.available_proxies:
                    self.available_proxies.remove(proxy)
                    self.unavailable_proxies.append(proxy)

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
                if proxy in self.available_proxies:
                    self.available_proxies.remove(proxy)
                    self.unavailable_proxies.append(proxy)

    def get_stats(self) -> Dict[str, int]:
        with self.lock:
            return {
                "available": len(self.available_proxies),
                "unavailable": len(self.unavailable_proxies),
                "total": len(self.available_proxies) + len(self.unavailable_proxies),
            }
