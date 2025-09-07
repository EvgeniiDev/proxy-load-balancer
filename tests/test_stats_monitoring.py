import unittest
import time
import json
from tests.base_test import BaseLoadBalancerTest


class TestStatsMonitoring(BaseLoadBalancerTest):
    """Тесты статистики и мониторинга"""
    
    def test_basic_stats_collection(self):
        """Тест базового сбора статистики"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            stats_interval=1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем несколько запросов
        for i in range(6):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем статистику серверов
        stats = self.server_manager.get_server_stats()
        
        # Каждый сервер должен получить 3 запроса при round_robin
        for port in ports:
            self.assertEqual(stats.get(port, 0), 3, 
                           f"Server {port} should have 3 requests")
        
        # Проверяем общую статистику
        total_requests = sum(stats.values())
        self.assertEqual(total_requests, 6)
    
    def test_stats_with_failures(self):
        """Тест статистики при ошибках"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Настраиваем один сервер на возврат ошибок
        mapping = {ports[0]: 500, ports[1]: 200}
        self.server_manager.set_fixed_response_codes(mapping)
        
        success_count = 0
        error_count = 0
        
        # Делаем запросы
        for i in range(8):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/status/200"
                )
                if response.status_code == 200:
                    success_count += 1
                elif response.status_code == 500:
                    error_count += 1
            except Exception:
                error_count += 1
        
        # Проверяем статистику
        stats = self.server_manager.get_server_stats()
        
        # Оба сервера должны получить запросы
        self.assertGreater(stats.get(ports[0], 0), 0, "Error server should receive requests")
        self.assertGreater(stats.get(ports[1], 0), 0, "Success server should receive requests")
        
        # Должны быть и успешные, и неуспешные запросы
        self.assertGreater(success_count, 0, "Should have successful requests")
    
    def test_performance_metrics(self):
        """Тест метрик производительности"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        config_path = self.create_test_config(
            proxies=proxies,
            stats_interval=1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Измеряем время выполнения запросов
        response_times = []
        
        for i in range(5):
            start_time = time.time()
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/delay/0.1"  # Небольшая задержка
            )
            end_time = time.time()
            
            self.assertEqual(response.status_code, 200)
            response_times.append(end_time - start_time)
        
        # Проверяем, что время ответа разумное
        avg_response_time = sum(response_times) / len(response_times)
        self.assertLess(avg_response_time, 5.0, "Average response time should be reasonable")
        self.assertGreater(avg_response_time, 0.05, "Response time should include network delay")
        
        # Проверяем статистику запросов
        stats = self.server_manager.get_server_stats()
        self.assertEqual(stats.get(server.port, 0), 5)
    
    def test_concurrent_request_stats(self):
        """Тест статистики при конкурентных запросах"""
        import threading
        
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        results = []
        threads = []
        
        def make_request(thread_id):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url=f"http://httpbin.org/get?thread={thread_id}"
                )
                results.append(response.status_code)
            except Exception as e:
                results.append(str(e))
        
        # Запускаем 12 конкурентных запросов
        for i in range(12):
            thread = threading.Thread(target=make_request, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Ждем завершения всех потоков
        for thread in threads:
            thread.join(timeout=10)
        
        # Проверяем результаты
        successful_requests = sum(1 for r in results if r == 200)
        self.assertGreaterEqual(successful_requests, 8, 
                               "Most concurrent requests should succeed")
        
        # Проверяем распределение по серверам
        stats = self.server_manager.get_server_stats()
        total_handled = sum(stats.values())
        self.assertGreaterEqual(total_handled, 8, 
                               "Servers should handle most requests")
    
    def test_stats_reset_functionality(self):
        """Тест сброса статистики"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        config_path = self.create_test_config(proxies=proxies)
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем запросы
        for i in range(3):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем, что статистика есть
        stats_before = self.server_manager.get_server_stats()
        self.assertEqual(stats_before.get(server.port, 0), 3)
        
        # Сбрасываем статистику
        self.server_manager.reset_stats()
        
        # Проверяем, что статистика сброшена
        stats_after = self.server_manager.get_server_stats()
        self.assertEqual(stats_after.get(server.port, 0), 0)
        
        # Делаем новые запросы
        for i in range(2):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем новую статистику
        stats_new = self.server_manager.get_server_stats()
        self.assertEqual(stats_new.get(server.port, 0), 2)
    
    def test_health_check_monitoring(self):
        """Тест мониторинга health check'ов"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            health_check_interval=1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем запросы для проверки работы
        for i in range(4):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Останавливаем один сервер
        self.server_manager.stop_server(ports[0])
        
        # Ждем health check
        self.wait_for_health_check(2)
        
        # Делаем запросы, они должны идти только на работающий сервер
        for i in range(4):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем статистику
        stats = self.server_manager.get_server_stats()
        
        # Первый сервер должен иметь 2 запроса (до остановки)
        self.assertEqual(stats.get(ports[0], 0), 2)
        
        # Второй сервер должен иметь 6 запросов (2 до + 4 после остановки первого)
        self.assertEqual(stats.get(ports[1], 0), 6)
    
    def test_error_rate_monitoring(self):
        """Тест мониторинга уровня ошибок"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Настраиваем различные коды ответа
        mapping = {ports[0]: 200, ports[1]: 404}
        self.server_manager.set_fixed_response_codes(mapping)
        
        response_codes = []
        
        # Делаем запросы
        for i in range(10):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/status/200"
                )
                response_codes.append(response.status_code)
            except Exception:
                response_codes.append(0)  # Ошибка соединения
        
        # Анализируем коды ответа
        success_count = sum(1 for code in response_codes if code == 200)
        error_count = sum(1 for code in response_codes if code != 200)
        
        # При round_robin должно быть 50/50 распределение
        self.assertEqual(success_count, 5, "Should have 5 successful responses")
        self.assertEqual(error_count, 5, "Should have 5 error responses")
        
        # Проверяем статистику серверов
        stats = self.server_manager.get_server_stats()
        self.assertEqual(stats.get(ports[0], 0), 5, "Success server should handle 5 requests")
        self.assertEqual(stats.get(ports[1], 0), 5, "Error server should handle 5 requests")
    
    def test_throughput_measurement(self):
        """Тест измерения пропускной способности"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        config_path = self.create_test_config(proxies=proxies)
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Измеряем пропускную способность
        start_time = time.time()
        request_count = 20
        
        for i in range(request_count):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Вычисляем requests per second
        rps = request_count / duration
        
        # Проверяем разумную пропускную способность
        self.assertGreater(rps, 1.0, "Should handle at least 1 request per second")
        self.assertLess(duration, 30.0, "20 requests should complete within 30 seconds")
        
        # Проверяем статистику
        stats = self.server_manager.get_server_stats()
        self.assertEqual(stats.get(server.port, 0), request_count)


if __name__ == '__main__':
    unittest.main()
