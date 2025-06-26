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
from tests.mock_socks5_server import MockSocks5ServerManager


class BaseLoadBalancerTest(unittest.TestCase):
    """Базовый класс для тестов load balancer'а"""
    
    def setUp(self):
        """Настройка для каждого теста"""
        self.server_manager = MockSocks5ServerManager()
        self.temp_configs = []
        self.balancer_process = None
        
    def tearDown(self):
        """Очистка после каждого теста"""
        # Останавливаем балансировщик
        if self.balancer_process:
            self.balancer_process.terminate()
            self.balancer_process.wait(timeout=5)
            
        # Останавливаем все mock серверы
        self.server_manager.stop_all()
        
        # Удаляем временные конфигурационные файлы
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
                          max_retries: int = 3) -> str:
        """Создает временный конфигурационный файл для тестов"""
        
        config = {
            "server": {
                "host": "127.0.0.1",
                "port": server_port
            },
            "proxies": proxies,
            "load_balancing_algorithm": algorithm,
            "health_check_interval": health_check_interval,
            "connection_timeout": connection_timeout,
            "max_retries": max_retries
        }
        
        # Создаем временный файл
        fd, config_path = tempfile.mkstemp(suffix='.json', prefix='test_config_')
        self.temp_configs.append(config_path)
        
        with os.fdopen(fd, 'w') as f:
            json.dump(config, f, indent=2)
            
        return config_path
        
    def start_balancer_with_config(self, config_path: str, wait_for_start: float = 0.5) -> int:
        """Запускает балансировщик с указанной конфигурацией"""
        with open(config_path) as f:
            config = json.load(f)
            
        # Если порт не указан, найдем свободный
        if config['server']['port'] == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', 0))
                config['server']['port'] = s.getsockname()[1]
                
            # Обновляем конфигурационный файл
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
                
        self.balancer_process = subprocess.Popen([
            'python', 'main.py', '-c', config_path, '-v'
        ])
        
        time.sleep(wait_for_start)
        
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
            'http': f'http://{balancer_host}:{balancer_port}',
            'https': f'http://{balancer_host}:{balancer_port}'
        }
        
        response = requests.request(method, target_url, proxies=proxies, data=data, headers=headers, timeout=timeout, verify=False)
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
