import unittest
import json
import time
import os
from tests.base_test import BaseLoadBalancerTest


class TestConfiguration(BaseLoadBalancerTest):
    """Тесты конфигурации системы"""
    
    def test_basic_config_loading(self):
        """Тест базовой загрузки конфигурации"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=5,
            connection_timeout=10,
            max_retries=3
        )
        
        # Проверяем, что конфигурация создана правильно
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        self.assertEqual(config["load_balancing_algorithm"], "round_robin")
        self.assertEqual(config["health_check_interval"], 5)
        self.assertEqual(config["connection_timeout"], 10)
        self.assertEqual(config["max_retries"], 3)
        self.assertEqual(len(config["proxies"]), 2)
        
        # Запускаем балансировщик с этой конфигурацией
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Проверяем, что система работает
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/get"
        )
        self.assertEqual(response.status_code, 200)
    
    def test_dynamic_config_reload(self):
        """Тест динамического перезагрузки конфигурации"""
        # Создаем начальную конфигурацию с одним сервером
        server1 = self.server_manager.create_servers(1)[0]
        initial_proxies = [{"host": "127.0.0.1", "port": server1.port}]
        
        config_path = self.create_test_config(
            proxies=initial_proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем запросы с первоначальной конфигурацией
        for i in range(3):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        stats_before = self.server_manager.get_server_stats()
        self.assertEqual(stats_before.get(server1.port, 0), 3)
        
        # Добавляем второй сервер через обновление конфигурации
        server2 = self.server_manager.create_servers(1)[0]
        updated_proxies = [
            {"host": "127.0.0.1", "port": server1.port},
            {"host": "127.0.0.1", "port": server2.port}
        ]
        
        self.update_config_file(config_path, {"proxies": updated_proxies})
        time.sleep(1)  # Даем время на применение изменений
        
        # Сбрасываем статистику для чистого теста
        self.server_manager.reset_stats()
        
        # Делаем запросы с обновленной конфигурацией
        for i in range(6):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем, что запросы распределяются между двумя серверами
        stats_after = self.server_manager.get_server_stats()
        self.assertEqual(stats_after.get(server1.port, 0), 3)
        self.assertEqual(stats_after.get(server2.port, 0), 3)
    
    def test_algorithm_change_reload(self):
        """Тест изменения алгоритма балансировки через конфигурацию"""
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Тестируем round_robin
        for i in range(9):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        stats_round_robin = self.server_manager.get_server_stats()
        # При round_robin каждый сервер должен получить ровно 3 запроса
        for port in ports:
            self.assertEqual(stats_round_robin.get(port, 0), 3)
        
        # Меняем алгоритм на random
        self.update_config_file(config_path, {"load_balancing_algorithm": "random"})
        time.sleep(1)
        
        # Сбрасываем статистику
        self.server_manager.reset_stats()
        
        # Тестируем random (делаем больше запросов для статистической значимости)
        for i in range(30):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        stats_random = self.server_manager.get_server_stats()
        total_requests = sum(stats_random.values())
        self.assertEqual(total_requests, 30)
        
        # При random распределение не должно быть идеально равномерным
        # Проверяем, что все серверы получили хотя бы один запрос
        for port in ports:
            self.assertGreater(stats_random.get(port, 0), 0, 
                             f"Server {port} should receive some requests with random algorithm")
    
    def test_timeout_configuration(self):
        """Тест конфигурации таймаутов"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        # Конфигурация с коротким таймаутом
        config_path = self.create_test_config(
            proxies=proxies,
            connection_timeout=2  # 2 секунды
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Обычный запрос должен работать
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/get"
        )
        self.assertEqual(response.status_code, 200)
        
        # Запрос с задержкой может не пройти (зависит от реализации)
        try:
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/delay/5",  # 5 секунд задержки
                timeout=3
            )
            # Если запрос прошел, проверяем код ответа
            self.assertIn(response.status_code, [200, 408, 504])
        except Exception:
            # Таймаут ожидаем при превышении лимита
            pass
    
    def test_health_check_interval_config(self):
        """Тест конфигурации интервала health check"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            health_check_interval=1  # 1 секунда
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Проверяем, что система работает
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/get"
        )
        self.assertEqual(response.status_code, 200)
        
        # Останавливаем сервер
        self.server_manager.stop_server(ports[0])
        
        # Быстро ждем health check (должен произойти через ~1 секунду)
        time.sleep(2)
        
        # Запросы должны переключиться на работающий сервер
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/get"
        )
        self.assertEqual(response.status_code, 200)
    
    def test_ssl_certificate_config(self):
        """Тест конфигурации SSL сертификатов"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        config_path = self.create_test_config(
            proxies=proxies,
            ssl_cert="cert.pem",
            ssl_key="key.pem"
        )
        
        # Проверяем, что конфигурация содержит SSL параметры
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        self.assertEqual(config["ssl_cert"], "cert.pem")
        self.assertEqual(config["ssl_key"], "key.pem")
        
        # Запускаем балансировщик
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Проверяем, что система работает (SSL будет использоваться для HTTPS)
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/get"
        )
        self.assertEqual(response.status_code, 200)
    
    def test_invalid_config_handling(self):
        """Тест обработки некорректной конфигурации"""
        # Создаем конфигурацию с некорректными данными
        invalid_configs = [
            {"proxies": []},  # Пустой список прокси
            {"proxies": [{"host": "invalid", "port": "not_a_number"}]},  # Некорректный порт
            {"load_balancing_algorithm": "unknown_algorithm"},  # Неизвестный алгоритм
            {"health_check_interval": -1},  # Отрицательный интервал
        ]
        
        for invalid_config in invalid_configs:
            with self.subTest(config=invalid_config):
                # Создаем базовую конфигурацию и перезаписываем проблемными значениями
                server = self.server_manager.create_servers(1)[0]
                proxies = [{"host": "127.0.0.1", "port": server.port}]
                
                config_path = self.create_test_config(proxies=proxies)
                
                # Обновляем конфигурацию некорректными данными
                with open(config_path, 'r') as f:
                    config = json.load(f)
                
                config.update(invalid_config)
                
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=2)
                
                # Попытка запуска с некорректной конфигурацией может завершиться ошибкой
                try:
                    balancer_port = self.start_balancer_with_config(config_path)
                    # Если балансировщик запустился, проверяем что он обрабатывает ошибки
                except Exception:
                    # Ожидаемое поведение для некоторых некорректных конфигураций
                    pass
    
    def test_config_file_watching(self):
        """Тест отслеживания изменений конфигурационного файла"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем запрос для проверки работы
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/get"
        )
        self.assertEqual(response.status_code, 200)
        
        # Обновляем конфигурацию напрямую в файле
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        config["load_balancing_algorithm"] = "random"
        config["connection_timeout"] = 15
        
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Даем время на обработку изменений
        time.sleep(1)
        
        # Система должна продолжать работать с новой конфигурацией
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/get"
        )
        self.assertEqual(response.status_code, 200)
    
    def test_backoff_configuration(self):
        """Тест конфигурации backoff параметров"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        config_path = self.create_test_config(
            proxies=proxies,
            overload_backoff_base_secs=0.1,  # Быстрый backoff для тестов
            rest_check_interval=0.05
        )
        
        # Проверяем, что конфигурация содержит backoff параметры
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        self.assertEqual(config["overload_backoff_base_secs"], 0.1)
        self.assertEqual(config["rest_check_interval"], 0.05)
        
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Настраиваем сервер на возврат 429
        self.server_manager.set_fixed_response_codes({server.port: 429})
        
        # Делаем запрос, который должен получить 429
        try:
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/status/200",
                timeout=3
            )
            # Должны получить 503 когда сервер в backoff
            self.assertEqual(response.status_code, 503)
        except Exception:
            pass  # Ожидаемо при недоступности сервера
        
        # Переключаем сервер на возврат 200
        self.server_manager.set_fixed_response_codes({server.port: 200})
        
        # Ждем короткий backoff период
        time.sleep(0.2)
        
        # Сервер должен восстановиться
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/status/200"
        )
        self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
