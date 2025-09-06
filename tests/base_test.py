import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from typing import Dict, List, Any, Optional
import requests
from urllib.parse import urlparse, urlunparse
from tests.mock_socks5_server import MockSocks5ServerManager


class BaseLoadBalancerTest(unittest.TestCase):
    def setUp(self):
        self.server_manager = MockSocks5ServerManager()
        self.temp_configs = []
        self.balancer = None
        self.config_manager = None
        
    def tearDown(self):
        if self.balancer:
            try:
                self.balancer.stop()
            except Exception:
                pass
        if self.config_manager:
            try:
                self.config_manager.stop_monitoring()
            except Exception:
                pass
        self.server_manager.stop_all()
        for config_path in self.temp_configs:
            try:
                os.unlink(config_path)
            except:
                pass
                
    def create_test_config(self, 
                          proxies: List[Dict[str, Any]], 
                          algorithm: str = "round_robin",
                          server_port: int = 0,
                          health_check_interval: int = 1,
                          connection_timeout: int = 5,
                          max_retries: int = 3,
                          **extra) -> str:
        config = {
            "server": {
                "host": "127.0.0.1",
                "port": 0 if server_port == 0 else server_port,
            },
            "ssl_cert": "cert.pem",
            "ssl_key": "key.pem",
            "proxies": proxies,
            "load_balancing_algorithm": algorithm,
            "health_check_interval": health_check_interval,
            "connection_timeout": connection_timeout,
            "max_retries": max_retries,
            "overload_backoff_base_secs": 0.2,
            "rest_check_interval": 0.05,
            "stats_interval": 1,
        }
        for k, v in extra.items():
            config[k] = v
        fd, config_path = tempfile.mkstemp(suffix='.json', prefix='test_config_')
        self.temp_configs.append(config_path)
        with os.fdopen(fd, 'w') as f:
            json.dump(config, f, indent=2)
        return config_path
        
    def start_balancer_with_config(self, config_path: str, wait_for_start: float = 0.5) -> int:
        from proxy_load_balancer.proxy_balancer import ProxyBalancer
        from proxy_load_balancer.config import ConfigManager
        with open(config_path) as f:
            config = json.load(f)
        if config['server']['port'] == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', 0))
                config['server']['port'] = s.getsockname()[1]
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
        self.config_manager = ConfigManager(config_path)
        cfg = self.config_manager.get_config()
        self.balancer = ProxyBalancer(cfg, verbose=True)
        def on_config_change(new_cfg):
            self.balancer.update_proxies(new_cfg)
            self.balancer.reload_algorithm()
        self.balancer.set_config_manager(self.config_manager, on_config_change)
        self.config_manager.add_change_callback(on_config_change)
        self.config_manager.start_monitoring()
        self.balancer.start()
        self.server_manager.balancer = self.balancer
        time.sleep(wait_for_start)
        try:
            return int(self.balancer.https_proxy.server_socket.getsockname()[1])
        except Exception:
            return config['server']['port']
            
    def make_request_through_proxy(self, 
                                  balancer_host: str = "127.0.0.1", 
                                  balancer_port: int = 8080,
                                  target_url: str = "http://httpbin.org/ip",
                                  method: str = "GET",
                                  data: Any = None,
                                  headers: Optional[Dict[str, str]] = None,
                                  timeout: int = 10) -> requests.Response:
        """Делает HTTP запрос через прокси балансировщик"""
        
        proxies = {
            'http': f'https://{balancer_host}:{balancer_port}',
            'https': f'https://{balancer_host}:{balancer_port}'
        }
        req_headers = dict(headers or {})
        if target_url.startswith('https://'):
            req_headers['X-Forwarded-Proto'] = 'https'
        response = requests.request(method, target_url, proxies=proxies, data=data, headers=req_headers, timeout=timeout, verify=False)
        return response
    
    def wait_for_health_check(self, seconds: float = 2):
        """Ждет выполнения health check'а"""
        time.sleep(seconds)
        
    def assert_request_distribution(self, 
                                   expected_distribution: Dict[int, int], 
                                   tolerance: float = 0.1):
        """Проверяет распределение запросов между серверами"""
        actual_stats = self.server_manager.get_server_stats()
        total_requests = sum(actual_stats.values())
        
        if total_requests == 0:
            self.fail("No requests were distributed")
            
        for port, expected_count in expected_distribution.items():
            actual_count = actual_stats.get(port, 0)
            expected_ratio = expected_count / sum(expected_distribution.values())
            actual_ratio = actual_count / total_requests
            
            self.assertAlmostEqual(
                actual_ratio, 
                expected_ratio, 
                delta=tolerance,
                msg=f"Distribution mismatch for port {port}: "
                    f"expected {expected_ratio:.2%}, got {actual_ratio:.2%}"
            )
            
    def update_config_file(self, config_path: str, updates: Dict[str, Any]):
        """Обновляет конфигурационный файл"""
        with open(config_path, 'r') as f:
            config = json.load(f)
            
        # Применяем обновления
        def update_nested_dict(d, updates):
            for key, value in updates.items():
                if isinstance(value, dict) and key in d and isinstance(d[key], dict):
                    update_nested_dict(d[key], value)
                else:
                    d[key] = value
                    
        update_nested_dict(config, updates)
        
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
            
        # Даем время на обработку изменений
        time.sleep(0.5)
