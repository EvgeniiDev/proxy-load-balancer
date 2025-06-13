import threading
import time
import random
from typing import List, Dict, Optional, Any
import requests
from .server import ProxyBalancerServer
from .handler import ProxyHandler
from .utils import ProxyManager


class ProxyBalancer:
    """Основной класс прокси-балансировщика"""
    
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

    def start(self) -> None:
        """Запуск балансировщика"""
        self._init_proxies()
        self._start_server()
        self._start_health_checker()

    def stop(self) -> None:
        """Остановка балансировщика"""
        self.stop_event.set()
        
        if self.health_thread:
            self.health_thread.join(timeout=5)
            
        if self.server:
            self.server.shutdown()
            self.server.server_close()

        for session in self.sessions.values():
            try:
                session.close()
            except:
                pass    def _init_proxies(self) -> None:
        """Инициализация прокси серверов"""
        for proxy_config in self.config['proxies']:
            proxy = {'host': proxy_config['host'], 'port': proxy_config['port']}
            session = self._create_session(proxy)
            self.sessions[ProxyManager.get_proxy_key(proxy)] = session
            
            if self._test_proxy(session):
                self.available_proxies.append(proxy)
            else:
                self.unavailable_proxies.append(proxy)

    def _create_session(self, proxy: Dict[str, Any]) -> requests.Session:
        """Создание HTTP сессии для прокси"""
        session: requests.Session = requests.Session()
        session.proxies = {
            'http': f"socks5://{proxy['host']}:{proxy['port']}",
            'https': f"socks5://{proxy['host']}:{proxy['port']}"
        }
        return session

    def _test_proxy(self, session: requests.Session) -> bool:
        """Тестирование работоспособности прокси"""
        test_urls: List[str] = ['http://httpbin.org/ip', 'http://icanhazip.com']
        for url in test_urls:
            try:
                response: requests.Response = session.get(url, timeout=10)
                if response.status_code == 200:
                    return True
            except:
                continue
        return False

    def _start_server(self) -> None:
        """Запуск HTTP сервера"""
        server_config: Dict[str, Any] = self.config['server']
        self.server = ProxyBalancerServer(
            (server_config['host'], server_config['port']), 
            ProxyHandler
        )
        self.server.proxy_balancer = self
        
        server_thread: threading.Thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        server_thread.start()

    def _start_health_checker(self) -> None:
        """Запуск проверки здоровья прокси"""
        self.health_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.health_thread.start()

    def _health_check_loop(self) -> None:
        """Цикл проверки здоровья прокси"""
        while not self.stop_event.is_set():
            self._check_unavailable_proxies()
            self._check_available_proxies()
            self.stop_event.wait(self.config['health_check_interval'])

    def _check_unavailable_proxies(self) -> None:
        """Проверка недоступных прокси на восстановление"""
        if not self.unavailable_proxies:
            return
            
        for proxy in self.unavailable_proxies[:2]:
            if self.stop_event.is_set():
                break
                
            session: requests.Session = self.sessions[ProxyManager.get_proxy_key(proxy)]
            if self._test_proxy(session):
                with self.lock:
                    if proxy in self.unavailable_proxies:
                        self.unavailable_proxies.remove(proxy)
                        self.available_proxies.append(proxy)
                        self.failure_counts[ProxyManager.get_proxy_key(proxy)] = 0

    def _check_available_proxies(self) -> None:
        """Проверка доступных прокси на работоспособность"""
        if not self.available_proxies:
            return
            
        proxy: Dict[str, Any] = random.choice(self.available_proxies)
        session: requests.Session = self.sessions[ProxyManager.get_proxy_key(proxy)]
        
        if not self._test_proxy(session):
            with self.lock:
                if proxy in self.available_proxies:
                    self.available_proxies.remove(proxy)
                    self.unavailable_proxies.append(proxy)

    def get_next_proxy(self) -> Optional[Dict[str, Any]]:
        """Получение следующего доступного прокси"""
        with self.lock:
            if not self.available_proxies:
                return None
            return random.choice(self.available_proxies)

    def get_session(self, proxy: Dict[str, Any]) -> Optional[requests.Session]:
        """Получение HTTP сессии для прокси"""
        return self.sessions.get(ProxyManager.get_proxy_key(proxy))

    def mark_success(self, proxy: Dict[str, Any]) -> None:
        """Отметка успешного использования прокси"""
        key: str = ProxyManager.get_proxy_key(proxy)
        if key in self.failure_counts:
            self.failure_counts[key] = 0

    def mark_failure(self, proxy: Dict[str, Any]) -> None:
        """Отметка неудачного использования прокси"""
        key: str = ProxyManager.get_proxy_key(proxy)
        self.failure_counts[key] = self.failure_counts.get(key, 0) + 1
        
        if self.failure_counts[key] >= self.config['max_retries']:
            with self.lock:
                if proxy in self.available_proxies:
                    self.available_proxies.remove(proxy)
                    self.unavailable_proxies.append(proxy)

    def get_stats(self) -> Dict[str, int]:
        """Получение статистики прокси"""
        with self.lock:
            return {
                'available': len(self.available_proxies),
                'unavailable': len(self.unavailable_proxies),
                'total': len(self.available_proxies) + len(self.unavailable_proxies)
            }
