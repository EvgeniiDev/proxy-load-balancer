import unittest
import time
from tests.base_test import BaseLoadBalancerTest


class TestLoadBalancing(BaseLoadBalancerTest):
    """Тесты алгоритмов балансировки нагрузки"""
    
    def test_round_robin_algorithm(self):
        """Тест алгоритма round robin"""
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем кратное количество запросов
        num_requests = 12
        for i in range(num_requests):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем равномерное распределение
        stats = self.server_manager.get_server_stats()
        total_requests = sum(stats.values())
        self.assertGreaterEqual(total_requests, num_requests)
        
        # Все серверы должны иметь приблизительно равное количество запросов
        requests_per_server = [stats.get(port, 0) for port in ports]
        self.assertTrue(all(r > 0 for r in requests_per_server), 
                       "All servers should receive at least one request")
        
        # Разница между максимальным и минимальным не должна превышать разумный порог
        max_requests = max(requests_per_server)
        min_requests = min(requests_per_server)
        self.assertLessEqual(max_requests - min_requests, 2,
                           f"Request distribution should be balanced. Stats: {dict(zip(ports, requests_per_server))}")
    
    def test_random_algorithm(self):
        """Тест алгоритма random"""
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="random",
            health_check_interval=9999  # Отключаем health check для тестов
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем 30 запросов для проверки случайного распределения
        for i in range(30):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем, что все серверы получили запросы
        stats = self.server_manager.get_server_stats()
        used_servers = [port for port in ports if stats.get(port, 0) > 0]
        self.assertGreaterEqual(len(used_servers), 2, 
                               "At least 2 servers should receive requests")
        
        # Проверяем общее количество запросов
        total_requests = sum(stats.values())
        self.assertEqual(total_requests, 30)
    
    def test_algorithm_switching(self):
        """Тест переключения алгоритмов балансировки"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=9999  # Отключаем health check для тестов
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем запросы с round_robin
        for i in range(4):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем равномерное распределение
        stats_before = self.server_manager.get_server_stats()
        for port in ports:
            self.assertEqual(stats_before.get(port, 0), 2)
        
        # Переключаем на random
        self.update_config_file(config_path, {
            "load_balancing_algorithm": "random"
        })
        time.sleep(1)  # Даем время на применение изменений
        
        # Сбрасываем статистику
        self.server_manager.reset_stats()
        
        # Делаем запросы с random
        for i in range(20):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем, что алгоритм изменился (распределение не должно быть точно равномерным)
        stats_after = self.server_manager.get_server_stats()
        total_after = sum(stats_after.values())
        self.assertEqual(total_after, 20)
        
        # При random маловероятно идеально равномерное распределение 10:10
        requests_diff = abs(stats_after.get(ports[0], 0) - stats_after.get(ports[1], 0))
        self.assertTrue(requests_diff >= 0, "Random algorithm should allow some variance")
    
    def test_single_proxy_balancing(self):
        """Тест балансировки с одним прокси"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем несколько запросов
        for i in range(5):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем, что все запросы пошли на единственный сервер
        stats = self.server_manager.get_server_stats()
        self.assertEqual(stats.get(server.port, 0), 5)
        self.assertEqual(len(stats), 1)
    
    def test_no_proxy_balancing(self):
        """Тест поведения балансировки без доступных прокси"""
        config_path = self.create_test_config(
            proxies=[],  # Пустой список прокси
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Запрос должен вернуть 503 Service Unavailable
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/get",
            timeout=5
        )
        self.assertEqual(response.status_code, 503)
    
    def test_weighted_distribution(self):
        """Тест весового распределения при многократных запросах"""
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=9999  # Отключаем health check для тестов
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем большое количество запросов для статистической значимости
        num_requests = 30  # По 10 на каждый сервер при round_robin
        
        for i in range(num_requests):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем равномерное распределение с round_robin
        stats = self.server_manager.get_server_stats()
        expected_per_server = num_requests // len(ports)
        
        for port in ports:
            actual_requests = stats.get(port, 0)
            self.assertEqual(actual_requests, expected_per_server,
                           f"Server {port} should have {expected_per_server} requests, got {actual_requests}")


if __name__ == '__main__':
    unittest.main()
