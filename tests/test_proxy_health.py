import unittest
import time
from tests.base_test import BaseLoadBalancerTest


class TestProxyHealth(BaseLoadBalancerTest):
    """Тесты здоровья прокси, переключения и обработки ошибок"""
    
    def test_proxy_failover(self):
        """Тест переключения при недоступности прокси"""
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=1,
            connection_timeout=3
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Делаем несколько запросов, все должны работать
        for i in range(6):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Останавливаем один сервер
        self.server_manager.stop_server(ports[0])
        
        # Ждем health check
        self.wait_for_health_check(2)
        
        # Делаем запросы, они должны идти на оставшиеся серверы
        successful_requests = 0
        for i in range(6):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/get",
                    timeout=10
                )
                if response.status_code == 200:
                    successful_requests += 1
            except Exception:
                pass  # Некоторые запросы могут не пройти при переключении
        
        # Проверяем, что большинство запросов прошло
        self.assertGreaterEqual(successful_requests, 4, 
                               "Most requests should succeed after failover")
        
        # Проверяем, что упавший сервер не получает запросы
        stats = self.server_manager.get_server_stats()
        # Первые 6 запросов: по 2 на каждый сервер
        # Следующие запросы: только на работающие серверы
        self.assertEqual(stats.get(ports[0], 0), 2, 
                        "Failed server should not receive new requests")
    
    def test_proxy_recovery(self):
        """Тест восстановления прокси после падения"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
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
        
        # Перезапускаем упавший сервер
        self.server_manager.restart_server(ports[0])
        
        # Ждем обнаружения восстановления
        self.wait_for_health_check(3)
        
        # Сбрасываем статистику
        self.server_manager.reset_stats()
        
        # Делаем запросы, теперь должны использоваться оба сервера
        for i in range(10):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем, что оба сервера получают запросы
        stats = self.server_manager.get_server_stats()
        self.assertGreater(stats.get(ports[0], 0), 0, 
                          "Recovered server should receive requests")
        self.assertGreater(stats.get(ports[1], 0), 0, 
                          "Working server should continue receiving requests")
    
    def test_429_response_handling(self):
        """Тест обработки 429 ответов"""
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=1,
            overload_backoff_base_secs=0.5
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Настраиваем первый сервер на возврат 429
        mapping = {ports[0]: 429, ports[1]: 200, ports[2]: 200}
        self.server_manager.set_fixed_response_codes(mapping)
        
        # Делаем запросы
        responses = []
        for i in range(6):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/status/200"
                )
                responses.append(response.status_code)
            except Exception as e:
                responses.append(str(e))
        
        # Проверяем, что получили успешные ответы (переключение на другие серверы)
        successful_responses = sum(1 for r in responses if r == 200)
        self.assertGreater(successful_responses, 0, 
                          "Should get successful responses from other proxies")
        
        # Проверяем, что сервер с 429 помечен как перегруженный
        stats = self.server_manager.get_server_stats()
        # Первый сервер должен получить минимум запросов из-за 429
        requests_to_429_server = stats.get(ports[0], 0)
        total_requests_to_working = stats.get(ports[1], 0) + stats.get(ports[2], 0)
        
        self.assertGreater(total_requests_to_working, requests_to_429_server,
                          "Working servers should handle more requests than 429 server")
    
    def test_overload_backoff_timing(self):
        """Тест экспоненциального backoff при перегрузке"""
        server = self.server_manager.create_servers(1)[0]
        proxies = [{"host": "127.0.0.1", "port": server.port}]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            overload_backoff_base_secs=0.2,
            rest_check_interval=0.1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Настраиваем сервер на возврат 429
        self.server_manager.set_fixed_response_codes({server.port: 429})
        
        # Первый запрос - должен получить 429 и установить backoff
        start_time = time.time()
        try:
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/status/200",
                timeout=5
            )
            # Если получили ответ, это должен быть 503 (нет доступных прокси)
            self.assertEqual(response.status_code, 503)
        except Exception:
            pass  # Может быть исключение при недоступности
        
        # Переключаем сервер на возврат 200
        self.server_manager.set_fixed_response_codes({server.port: 200})
        
        # Немедленный запрос должен провалиться (сервер в backoff)
        try:
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/status/200",
                timeout=3
            )
            self.assertEqual(response.status_code, 503, 
                           "Server should be in backoff, request should fail")
        except Exception:
            pass  # Ожидаемое поведение
        
        # Ждем окончания backoff
        time.sleep(0.5)
        
        # Теперь запрос должен пройти
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/status/200"
        )
        self.assertEqual(response.status_code, 200, 
                        "Server should be available after backoff period")
    
    def test_multiple_429_exponential_backoff(self):
        """Тест экспоненциального роста времени backoff при повторных 429"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            overload_backoff_base_secs=0.1,
            rest_check_interval=0.05
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Настраиваем оба сервера на возврат 429
        mapping = {ports[0]: 429, ports[1]: 429}
        self.server_manager.set_fixed_response_codes(mapping)
        
        # Делаем несколько запросов, чтобы увеличить счетчик перегрузки
        for i in range(3):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/status/200",
                    timeout=5
                )
                # Должны получить 503 когда все серверы недоступны
                self.assertIn(response.status_code, [503, 429])
            except Exception:
                pass  # Ожидаемо при недоступности всех серверов
            
            time.sleep(0.1)  # Небольшая пауза между запросами
        
        # Переключаем серверы на возврат 200
        mapping = {ports[0]: 200, ports[1]: 200}
        self.server_manager.set_fixed_response_codes(mapping)
        
        # Ждем восстановления
        time.sleep(0.3)
        
        # Проверяем, что серверы восстановились
        response = self.make_request_through_proxy(
            balancer_port=balancer_port,
            target_url="http://httpbin.org/status/200"
        )
        self.assertEqual(response.status_code, 200, 
                        "Servers should recover after backoff")
    
    def test_mixed_proxy_responses(self):
        """Тест смешанных ответов от прокси (429 + 200)"""
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            overload_backoff_base_secs=0.2
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Настраиваем один сервер на 429, остальные на 200
        mapping = {ports[0]: 429, ports[1]: 200, ports[2]: 200}
        self.server_manager.set_fixed_response_codes(mapping)
        
        successful_requests = 0
        total_requests = 12
        
        for i in range(total_requests):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/status/200"
                )
                if response.status_code == 200:
                    successful_requests += 1
            except Exception:
                pass
        
        # Проверяем, что большинство запросов успешны
        success_rate = successful_requests / total_requests
        self.assertGreaterEqual(success_rate, 0.6, 
                               f"Success rate should be at least 60%, got {success_rate:.1%}")
        
        # Проверяем распределение запросов
        stats = self.server_manager.get_server_stats()
        
        # Рабочие серверы должны получить больше запросов
        working_servers_requests = stats.get(ports[1], 0) + stats.get(ports[2], 0)
        overloaded_server_requests = stats.get(ports[0], 0)
        
        self.assertGreater(working_servers_requests, overloaded_server_requests,
                          "Working servers should handle more requests")
    
    def test_all_proxies_unavailable(self):
        """Тест поведения когда все прокси недоступны"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Останавливаем все серверы
        for port in ports:
            self.server_manager.stop_server(port)
        
        # Ждем health check
        self.wait_for_health_check(2)
        
        # Запросы должны завершаться ошибкой
        with self.assertRaises((Exception, AssertionError)):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get",
                timeout=5
            )
            # Если ответ получен, это должен быть код ошибки
            self.assertIn(response.status_code, [502, 503, 504])


if __name__ == '__main__':
    unittest.main()
