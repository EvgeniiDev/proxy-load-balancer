import time
import threading
import concurrent.futures
from typing import Dict, List, Any, Optional
import collections
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .proxy_stats import ProxyStats
from .base import ProxyHandler, ConfigValidator, Logger
from .proxy_selector_algo import LoadBalancingAlgorithm, AlgorithmFactory
from .stats_reporter import StatsReporter
from .http_proxy import HTTPProxy


class ProxyBalancer:
    def __init__(self, config: Dict[str, Any], verbose: bool = False) -> None:
        self.logger = Logger.get_logger("proxy_balancer")
        self.config = config
        self.verbose = verbose
        # Throttle state for opportunistic probes on the hot path
        self._last_unavail_probe_ts = 0.0
        self._initialize_proxy_lists()
        self._initialize_stats()
        self._initialize_sync_objects()
        self._initialize_components()

    def _initialize_proxy_lists(self):
        """Инициализация списков прокси."""
        self.available_proxies: List[Dict[str, Any]] = self.config.get("proxies", [])
        self.unavailable_proxies: List[Dict[str, Any]] = []
        self.resting_proxies: Dict[str, Dict[str, Any]] = {}

    def _initialize_stats(self):
        """Инициализация статистики."""
        self.proxy_stats: Dict[str, ProxyStats] = {}
        self.max_session_pool_size = 5
        self.health_failures = {}

    def _initialize_sync_objects(self):
        """Инициализация объектов синхронизации."""
        self.stats_lock = threading.Lock()
        self.proxy_selection_lock = threading.Lock()
        self.health_check_stop_event = threading.Event()
    # Queue of proxies restored by background health checks to be used immediately

    def _initialize_components(self):
        """Инициализация компонентов."""
        self.server = None
        self.http_proxy = None
        self.health_check_thread = None
        self.stats_thread = None
        
        self.stats_reporter = StatsReporter(self)
        self._setup_load_balancer()
        self.http_proxy = HTTPProxy(self.config, self)

    def _setup_load_balancer(self):
        """Настройка алгоритма балансировки."""
        algorithm_name = ConfigValidator.get_config_value(
            self.config, "load_balancing_algorithm", "random"
        )
        try:
            self.load_balancer = AlgorithmFactory.create_algorithm(algorithm_name)
        except ValueError as e:
            self.logger.error(f"Algorithm initialization error: {e}")
            self.logger.info("Using default algorithm: random")
            self.load_balancer = AlgorithmFactory.create_algorithm("random")

    def get_raw_stats(self) -> Dict[str, Any]:
        return {
            'proxy_stats': self.proxy_stats,
            'available_proxies': self.available_proxies,
            'unavailable_proxies': self.unavailable_proxies,
            'load_balancer': self.load_balancer,
            'verbose': self.verbose
        }

    def get_stats(self) -> Dict[str, Any]:
        return self.stats_reporter.get_stats()

    def print_stats(self) -> None:
        self.stats_reporter.print_stats()

    def print_compact_stats(self) -> None:
        self.stats_reporter.print_compact_stats()

    def log_stats_summary(self) -> None:
        self.stats_reporter.log_stats_summary()

    def update_proxies(self, new_config: Dict[str, Any]):
        """Обновление списка прокси из новой конфигурации."""
        self.config = new_config
        new_proxies = new_config.get("proxies", [])
        new_proxy_keys = set(ProxyHandler.get_proxy_key(proxy) for proxy in new_proxies)

        self.logger.warning(f"Updated proxies before add: {len(self.available_proxies)}")

        with self.proxy_selection_lock:
            self.available_proxies = new_proxies
            # Drop unavailable proxies that are not in new config
            self.unavailable_proxies = [
                p for p in self.unavailable_proxies
                if ProxyHandler.get_proxy_key(p) in new_proxy_keys
            ]
            self._cleanup_resting_proxies(new_proxy_keys)
            try:
                if hasattr(self, 'load_balancer') and self.load_balancer:
                    self.load_balancer.reset()
            except Exception:
                pass

        self._cleanup_old_proxy_data(new_proxy_keys)
        self.logger.warning(f"Updated proxies after add: {len(self.available_proxies)}")

    def _cleanup_resting_proxies(self, current_proxy_keys: set):
        """Очистка отдыхающих прокси, которых больше нет в конфигурации."""
        resting_keys_to_remove = [
            key for key in self.resting_proxies 
            if key not in current_proxy_keys
        ]
        
        for key in resting_keys_to_remove:
            del self.resting_proxies[key]


    def reload_algorithm(self):
        """Перезагрузка алгоритма балансировки."""
        algorithm_name = ConfigValidator.get_config_value(
            self.config, "load_balancing_algorithm", "random"
        )
        try:
            old_algorithm = self.load_balancer
            self.load_balancer = AlgorithmFactory.create_algorithm(algorithm_name)
            # Сброс состояния старого алгоритма
            if old_algorithm:
                old_algorithm.reset()
        except ValueError:
            self.logger.error(f"Unknown algorithm: {algorithm_name}, using random")
            self.load_balancer = AlgorithmFactory.create_algorithm("random")

    def start(self):
        if self.http_proxy:
            self.http_thread = threading.Thread(target=self.http_proxy.start, daemon=True)
            self.http_thread.start()
            self.logger.info("HTTP Proxy started")
        
        self.stats_reporter.start_monitoring()
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
        unavailable_check_interval = float(self.config.get("rest_check_interval", max(0.01, health_check_interval // 6)))
        sleep_interval = min(health_check_interval, unavailable_check_interval)

        last_full_check = 0

        while not self.health_check_stop_event.is_set():
            current_time = time.time()

            # Always check resting proxies first
            if self.resting_proxies:
                self._check_resting_proxies()

            if self.unavailable_proxies:
                self._check_unavailable_proxies()

            if current_time - last_full_check >= health_check_interval:
                self._check_all_proxies()
                last_full_check = current_time

            self.health_check_stop_event.wait(sleep_interval)

    def _check_unavailable_proxies(self):
        if not self.unavailable_proxies:
            return
            
        proxies_to_check = list(self.unavailable_proxies)
        self.logger.info(f"Quick health check for {len(proxies_to_check)} unavailable proxies")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(proxies_to_check), 10)) as executor:
            future_to_proxy = {
                executor.submit(self._test_proxy_health, proxy): proxy 
                for proxy in proxies_to_check
            }
            
            for future in concurrent.futures.as_completed(future_to_proxy, timeout=15):
                proxy = future_to_proxy[future]
                try:
                    is_healthy = future.result()
                    key = ProxyHandler.get_proxy_key(proxy)
                    if is_healthy:
                        self.logger.info(f"Health check: proxy {key} is healthy; restoring")
                        self._restore_proxy(proxy)
                    else:
                        self.logger.info(f"Health check: proxy {key} still unhealthy")
                except Exception as e:
                    self.logger.debug(f"Health check failed for {ProxyHandler.get_proxy_key(proxy)}: {e}")

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
                key = ProxyHandler.get_proxy_key(proxy)
                with self.proxy_selection_lock:
                    if proxy in self.unavailable_proxies:
                        self.unavailable_proxies.remove(proxy)
                    if proxy not in self.available_proxies:
                        self.available_proxies.append(proxy)
                        self.logger.info(f"Proxy {key} restored to available pool")
                        if hasattr(self, 'load_balancer') and self.load_balancer:
                            self.load_balancer.reset()
                with self.stats_lock:
                    stats = self._get_or_create_proxy_stats(key)
                    stats.failure_count = 0
                    self.health_failures[key] = 0

    def _test_proxy_health(self, proxy: Dict[str, Any]) -> bool:
        """Treat proxy as healthy if TCP connection to SOCKS server succeeds.
        Avoids external network dependency during tests.
        """
        import socket
        try:
            with socket.create_connection((proxy["host"], int(proxy["port"])), timeout=2):
                return True
        except Exception:
            return False

    def _quick_test_proxy_health(self, proxy: Dict[str, Any], timeout: float = 1.0) -> bool:
        """Very fast health probe used inline on request path to speed up recovery."""
        import socket
        try:
            with socket.create_connection((proxy["host"], int(proxy["port"])), timeout=timeout):
                return True
        except Exception:
            return False

    def _restore_proxy(self, proxy: Dict[str, Any]):
        """Восстановление прокси в пул доступных."""
        key = ProxyHandler.get_proxy_key(proxy)
        with self.proxy_selection_lock:
            if proxy in self.unavailable_proxies:
                self.unavailable_proxies.remove(proxy)
            if proxy not in self.available_proxies:
                self.available_proxies.insert(0, proxy)
                self.logger.info(f"Proxy {key} restored to available pool")
                if hasattr(self, 'load_balancer') and self.load_balancer:
                    self.load_balancer.reset()
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.failure_count = 0
            self.health_failures[key] = 0

    def _check_resting_proxies(self):
        """Проверяет отдыхающие прокси и восстанавливает их при готовности"""
        current_time = time.time()
        proxies_to_restore = []
        
        with self.proxy_selection_lock:
            for key, rest_info in list(self.resting_proxies.items()):
                if current_time >= rest_info["rest_until"]:
                    proxy = rest_info["proxy"]
                    reason = rest_info.get("reason", "unknown")
                    # Always restore proxies after rest period, regardless of reason
                    if proxy not in self.available_proxies:
                        self.available_proxies.append(proxy)
                    proxies_to_restore.append(key)
                    self.logger.info(f"Proxy {key} restored from rest period (immediate)")
                    with self.stats_lock:
                        stats = self._get_or_create_proxy_stats(key)
                        stats.failure_count = 0
            for key in proxies_to_restore:
                if key in self.resting_proxies:
                    del self.resting_proxies[key]

    def _mark_proxy_unhealthy(self, proxy: Dict[str, Any]):
        key = ProxyHandler.get_proxy_key(proxy)
        
        with self.proxy_selection_lock:
            if proxy in self.available_proxies:
                self.available_proxies.remove(proxy)
            
            if proxy not in self.unavailable_proxies:
                self.unavailable_proxies.append(proxy)
                self.logger.warning(f"Proxy {key} marked as unhealthy via health check")
        with self.stats_lock:
            # Keep tracking from zero after marking
            self.health_failures[key] = 0
    def _run_initial_health_check(self):
        proxies = self.config.get("proxies", [])
        for proxy in proxies:
            if proxy not in self.available_proxies:
                self.available_proxies.append(proxy)

    def stop(self):
        self.health_check_stop_event.set()
        if self.health_check_thread:
            self.health_check_thread.join(timeout=5)
        
        if self.http_proxy:
            self.http_proxy.stop()
            if hasattr(self, 'http_thread'):
                self.http_thread.join(timeout=5)
            self.logger.info("HTTP Proxy stopped")

        self.stats_reporter.stop_monitoring()

    def set_config_manager(self, config_manager, on_config_change):
        self._config_manager = config_manager
        self._on_config_change = on_config_change

    def get_next_proxy(self) -> Optional[Dict[str, Any]]:
        with self.proxy_selection_lock:
            proxy = self.load_balancer.select_proxy(self.available_proxies)
            if proxy:
                key = ProxyHandler.get_proxy_key(proxy)
                self.logger.debug(f"Selected proxy {key}")
            return proxy
        if to_check and (now - self._last_unavail_probe_ts) >= 0.2:
            restored_any = False
            for p in to_check:
                try:
                    if self._quick_test_proxy_health(p, timeout=0.5):
                        # This will acquire the selection lock internally in a safe way
                        self._restore_proxy(p)
                        restored_any = True
                except Exception:
                    # Ignore any probing errors
                    pass
            self._last_unavail_probe_ts = now

        # If any unavailable proxy just became healthy, restore and use it immediately
        if to_check:
            for p in to_check:
                try:
                    if self._quick_test_proxy_health(p, timeout=1.0):
                        # Log and immediately return this freshly restored proxy
                        key = ProxyHandler.get_proxy_key(p)
                        self.logger.info(f"Quick-restore detected for proxy {key}; returning it immediately")
                        self._restore_proxy(p)
                        return p
                except Exception:
                    pass

        # Now select a proxy under lock
        with self.proxy_selection_lock:
            proxy = self.load_balancer.select_proxy(self.available_proxies)
            if proxy:
                key = ProxyHandler.get_proxy_key(proxy)
                self.logger.debug(f"Selected proxy {key}")
            return proxy

    def get_session(self, proxy: Dict[str, Any]) -> requests.Session:
        """Получение HTTP сессии для прокси."""
        key = ProxyHandler.get_proxy_key(proxy)
        
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            session = stats.get_session()
            
            if session:
                return session
            
            return self._create_new_session(proxy, stats)

    def _create_new_session(self, proxy: Dict[str, Any], stats: ProxyStats) -> requests.Session:
        """Создание новой HTTP сессии для прокси."""
        session = requests.Session()
        
        # Настройка прокси
        proxy_url = ProxyHandler.create_proxy_url(proxy)
        session.proxies = {
            "http": proxy_url,
            "https": proxy_url
        }
        
        # Настройка заголовков
        session.headers.update(self._get_default_headers())
        
        # Настройка адаптера
        adapter = self._create_http_adapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session

    def _get_default_headers(self) -> Dict[str, str]:
        """Получение стандартных заголовков для HTTP запросов."""
        return {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }

    def _create_http_adapter(self) -> HTTPAdapter:
        """Создание HTTP адаптера с настройками."""
        return HTTPAdapter(
            max_retries=Retry(total=0),
            pool_connections=10,
            pool_maxsize=20
        )

    def return_session(self, proxy: Dict[str, Any], session: requests.Session):
        key = ProxyHandler.get_proxy_key(proxy)
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.add_session(session, self.max_session_pool_size)

    def mark_success(self, proxy: Dict[str, Any]):
        """Отметка успешного выполнения запроса через прокси."""
        key = ProxyHandler.get_proxy_key(proxy)
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.increment_requests()
            stats.increment_successes()
            stats.increment_200()
            stats.reset_overload_count()
        
        # Восстанавливаем прокси только если он не в available
        with self.proxy_selection_lock:
            if proxy not in self.available_proxies:
                self._restore_proxy(proxy)
        
        self.logger.debug(f"Proxy {key} success (total: {stats.success_count})")

    def mark_failure(self, proxy: Dict[str, Any]):
        """Отметка неудачного выполнения запроса через прокси."""
        key = ProxyHandler.get_proxy_key(proxy)
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.increment_requests()
            stats.increment_failures()
            stats.increment_other()
            failure_count = stats.failure_count
        
        self.logger.warning(f"Proxy {key} failed (failure #{failure_count})")
        self._handle_proxy_failure(proxy, key, failure_count)

    def _handle_proxy_failure(self, proxy: Dict[str, Any], key: str, failure_count: int):
        """Обработка неудачи прокси."""
        max_retries = ConfigValidator.get_config_value(self.config, "max_retries", 3)
        
        if failure_count >= max_retries:
            with self.proxy_selection_lock:
                self.available_proxies = [
                    p for p in self.available_proxies 
                    if ProxyHandler.get_proxy_key(p) != key
                ]
                
                if proxy not in self.unavailable_proxies:
                    self.unavailable_proxies.append(proxy)
                
                self.logger.error(f"Proxy {key} marked as unavailable after {failure_count} failures")

    def mark_overloaded(self, proxy: Dict[str, Any]):
        """Отметка перегруженного прокси с переводом в режим отдыха."""
        key = ProxyHandler.get_proxy_key(proxy)
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.increment_overloads()
            overload_count = stats.overload_count
            
        self._put_proxy_to_rest(proxy, key, overload_count, "overloaded")

    def _put_proxy_to_rest(self, proxy: Dict[str, Any], key: str, overload_count: int, reason: str):
        """Перевод прокси в режим отдыха."""
        base_rest_duration = ConfigValidator.get_config_value(
            self.config, "overload_backoff_base_secs", 30
        )
        rest_duration = float(base_rest_duration) * max(1, overload_count)
        rest_until = time.time() + rest_duration
        
        with self.proxy_selection_lock:
            if proxy in self.available_proxies:
                self.available_proxies.remove(proxy)
            
            self.resting_proxies[key] = {
                "proxy": proxy,
                "rest_until": rest_until,
                "overload_count": overload_count,
                "reason": reason,
            }
            
        self.logger.warning(
            f"Proxy {key} {reason} (#{overload_count}), resting for {rest_duration}s"
        )

    def mark_429_response(self, proxy: Dict[str, Any]):
        """Отметка ответа 429 от прокси."""
        key = ProxyHandler.get_proxy_key(proxy)
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.increment_requests()
            stats.increment_failures()
            stats.increment_429()

        

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

    def _cleanup_old_proxy_data(self, current_proxy_keys: set):
        with self.stats_lock:
            orphaned_keys = set(self.proxy_stats.keys()) - current_proxy_keys
            
            for key in orphaned_keys:
                if key in self.proxy_stats:
                    self.proxy_stats[key].close_all_sessions()
                    del self.proxy_stats[key]

    def _get_or_create_proxy_stats(self, key: str) -> ProxyStats:
        if key not in self.proxy_stats:
            self.proxy_stats[key] = ProxyStats()
        return self.proxy_stats[key]
