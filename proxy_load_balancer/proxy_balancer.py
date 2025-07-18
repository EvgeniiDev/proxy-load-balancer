import time
import logging
import threading
import concurrent.futures
from typing import Dict, List, Any, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import socks

from .proxy_stats import ProxyStats
from .utils import ProxyManager
from .proxy_selector_algo import LoadBalancingAlgorithm, AlgorithmFactory
from .handler import ProxyHandler
from .server import ProxyBalancerServer
from .stats_reporter import StatsReporter


class ProxyBalancer:
    def __init__(self, config: Dict[str, Any], verbose: bool = False) -> None:
        self.config = config
        self.verbose = verbose
        
        self.available_proxies: List[Dict[str, Any]] = config.get("proxies", [])
        self.unavailable_proxies: List[Dict[str, Any]] = []
        self.resting_proxies: Dict[str, Dict[str, Any]] = {}
        
        self.proxy_stats: Dict[str, ProxyStats] = {}
        self.max_session_pool_size = 5
        
        self.stats_lock = threading.Lock()
        self.proxy_selection_lock = threading.Lock()
        
        self.server = None
        self.health_check_stop_event = threading.Event()
        self.health_check_thread = None
        self.stats_thread = None
        
        self.logger = logging.getLogger("proxy_balancer")
        self._setup_logger()
        
        self.stats_reporter = StatsReporter(self)
        
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
        self.config = new_config
        new_proxies = new_config.get("proxies", [])
        new_proxy_keys = set(ProxyManager.get_proxy_key(proxy) for proxy in new_proxies)

        self.logger.warning(f"Updated proxies before add: {len(self.available_proxies)}")

        with self.proxy_selection_lock:
            self.available_proxies = new_proxies
            self.unavailable_proxies = []
            
            # Очистка отдыхающих прокси, которых больше нет в конфигурации
            resting_keys_to_remove = []
            for key in self.resting_proxies:
                if key not in new_proxy_keys:
                    resting_keys_to_remove.append(key)
            
            for key in resting_keys_to_remove:
                del self.resting_proxies[key]

        self._cleanup_old_proxy_data(new_proxy_keys)

        self.logger.warning(f"Updated proxies after add: {len(self.available_proxies)}")


    def reload_algorithm(self):
        algorithm_name = self.config.get("load_balancing_algorithm", "random")
        try:
            self.load_balancer = AlgorithmFactory.create_algorithm(algorithm_name)
        except ValueError:
            self.logger.error(f"Unknown algorithm: {algorithm_name}, using random")
            self.load_balancer = AlgorithmFactory.create_algorithm("random")

    def start(self):
        host = self.config["server"]["host"]
        port = self.config["server"]["port"]
        self.server = ProxyBalancerServer((host, port), ProxyHandler)
        self.server.proxy_balancer = self
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.logger.info(f"ProxyBalancer server started on {host}:{port}")
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
        unavailable_check_interval = max(5, health_check_interval // 6)
        
        last_full_check = 0
        
        while not self.health_check_stop_event.is_set():
            current_time = time.time()
            
            if self.unavailable_proxies:
                self._check_unavailable_proxies()
            
            if self.resting_proxies:
                self._check_resting_proxies()
            
            if current_time - last_full_check >= health_check_interval:
                self._check_all_proxies()
                last_full_check = current_time
            
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
            sock = socks.socksocket()
            sock.set_proxy(
                proxy_type=socks.SOCKS5,
                addr=proxy["host"],
                port=proxy["port"],
                username=proxy.get("username"),
                password=proxy.get("password"),
            )
            sock.settimeout(5)
            
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
            
            if proxy not in self.available_proxies:
                self.available_proxies.append(proxy)
                self.logger.info(f"Proxy {key} restored to available pool")
                
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.failure_count = 0

    def _check_resting_proxies(self):
        """Проверяет отдыхающие прокси и восстанавливает их при готовности"""
        current_time = time.time()
        proxies_to_restore = []
        
        with self.proxy_selection_lock:
            for key, rest_info in list(self.resting_proxies.items()):
                if current_time >= rest_info["rest_until"]:
                    proxy = rest_info["proxy"]
                    reason = rest_info.get("reason", "unknown")
                    
                    # Для перегруженных прокси используем более мягкую проверку
                    if reason == "overloaded":
                        # Для перегруженных прокси просто возвращаем в пул
                        # без дополнительной проверки здоровья
                        self.available_proxies.append(proxy)
                        proxies_to_restore.append(key)
                        self.logger.info(f"Proxy {key} restored from overload rest period")
                        
                        with self.stats_lock:
                            stats = self._get_or_create_proxy_stats(key)
                            stats.failure_count = 0
                    else:
                        # Для других причин (неисправность) делаем проверку здоровья
                        if self._test_proxy_health(proxy):
                            self.available_proxies.append(proxy)
                            proxies_to_restore.append(key)
                            self.logger.info(f"Proxy {key} restored from rest period (health check passed)")
                            
                            with self.stats_lock:
                                stats = self._get_or_create_proxy_stats(key)
                                stats.failure_count = 0
                        else:
                            if proxy not in self.unavailable_proxies:
                                self.unavailable_proxies.append(proxy)
                            proxies_to_restore.append(key)
                            self.logger.warning(f"Proxy {key} moved to unavailable after rest period (health check failed)")
            
            for key in proxies_to_restore:
                if key in self.resting_proxies:
                    del self.resting_proxies[key]

    def _mark_proxy_unhealthy(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        
        with self.proxy_selection_lock:
            if proxy in self.available_proxies:
                self.available_proxies.remove(proxy)
            
            if proxy not in self.unavailable_proxies:
                self.unavailable_proxies.append(proxy)
                self.logger.warning(f"Proxy {key} marked as unhealthy via health check")

    def _run_initial_health_check(self):
        proxies = self.config.get("proxies", [])
        for proxy in proxies:
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

        self.stats_reporter.stop_monitoring()

    def set_config_manager(self, config_manager, on_config_change):
        self._config_manager = config_manager
        self._on_config_change = on_config_change

    def get_next_proxy(self) -> Optional[Dict[str, Any]]:
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
        
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            session = stats.get_session()
            
            if session:
                return session
            
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
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.add_session(session, self.max_session_pool_size)

    def mark_success(self, proxy: Dict[str, Any]):
        key = ProxyManager.get_proxy_key(proxy)
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.increment_successes()
            # Сбрасываем счетчик перегрузок при успешном запросе
            stats.reset_overload_count()
        
        self._restore_proxy(proxy)
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
                self.available_proxies = [p for p in self.available_proxies if ProxyManager.get_proxy_key(p) != key]
                
                if proxy not in self.unavailable_proxies:
                    self.unavailable_proxies.append(proxy)
                
                self.logger.error(f"Proxy {key} marked as unavailable after {failure_count} failures")

    def mark_overloaded(self, proxy: Dict[str, Any]):
        """Помечает прокси как перегруженную и отправляет на отдых"""
        key = ProxyManager.get_proxy_key(proxy)
        
        with self.stats_lock:
            stats = self._get_or_create_proxy_stats(key)
            stats.increment_overloads()
            overload_count = stats.overload_count
        
        # Адаптивное время отдыха: базовое время + дополнительное время за каждую перегрузку
        base_rest_duration = self.config.get("proxy_rest_duration", 300)
        adaptive_multiplier = self.config.get("overload_adaptive_multiplier", 0.5)
        max_multiplier = self.config.get("max_overload_multiplier", 3.0)
        
        calculated_multiplier = min(overload_count * adaptive_multiplier, max_multiplier)
        rest_duration = int(base_rest_duration * (1 + calculated_multiplier))
        rest_until = time.time() + rest_duration

        with self.proxy_selection_lock:
            if proxy in self.available_proxies:
                self.available_proxies.remove(proxy)
                self.resting_proxies[key] = {
                    "proxy": proxy,
                    "rest_until": rest_until,
                    "overload_count": overload_count,
                    "reason": "overloaded"
                }
                self.logger.warning(
                    f"Proxy {key} marked as overloaded (#{overload_count}), "
                    f"resting for {rest_duration} seconds (adaptive: {calculated_multiplier:.1f}x)"
                )

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
