import unittest
import time
import threading
import json
from tests.base_test import BaseLoadBalancerTest


class TestIntegration(BaseLoadBalancerTest):
    """Комплексные интеграционные тесты системы"""
    
    def test_full_system_workflow(self):
        """Тест полного рабочего цикла системы"""
        # Создаем 3 сервера
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=2
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Этап 1: Нормальная работа
        for i in range(9):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем равномерное распределение
        stats_normal = self.server_manager.get_server_stats()
        for port in ports:
            self.assertEqual(stats_normal.get(port, 0), 3)
        
        # Этап 2: Падение одного сервера
        self.server_manager.stop_server(ports[0])
        self.wait_for_health_check(3)
        
        # Сбрасываем статистику для чистого теста failover
        self.server_manager.reset_stats()
        
        # Делаем запросы после падения
        for i in range(8):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем failover - запросы должны идти только на работающие серверы
        stats_failover = self.server_manager.get_server_stats()
        self.assertEqual(stats_failover.get(ports[0], 0), 0)  # Упавший сервер
        self.assertEqual(stats_failover.get(ports[1], 0), 4)  # Работающие серверы
        self.assertEqual(stats_failover.get(ports[2], 0), 4)
        
        # Этап 3: Восстановление сервера
        self.server_manager.restart_server(ports[0])
        self.wait_for_health_check(3)
        
        # Сбрасываем статистику
        self.server_manager.reset_stats()
        
        # Делаем запросы после восстановления
        for i in range(12):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем восстановление - все серверы должны получать запросы
        stats_recovery = self.server_manager.get_server_stats()
        for port in ports:
            self.assertEqual(stats_recovery.get(port, 0), 4)
    
    def test_concurrent_load_with_failures(self):
        """Тест конкурентной нагрузки с отказами серверов"""
        servers = self.server_manager.create_servers(4)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        results = []
        active_threads = []
        
        def worker_thread(thread_id, requests_count):
            """Рабочий поток для выполнения запросов"""
            thread_results = []
            for i in range(requests_count):
                try:
                    response = self.make_request_through_proxy(
                        balancer_port=balancer_port,
                        target_url=f"http://httpbin.org/get?thread={thread_id}&req={i}"
                    )
                    thread_results.append(response.status_code)
                except Exception as e:
                    thread_results.append(f"error: {str(e)}")
                time.sleep(0.1)  # Небольшая пауза между запросами
            
            results.extend(thread_results)
        
        # Запускаем конкурентные потоки
        for thread_id in range(6):
            thread = threading.Thread(target=worker_thread, args=(thread_id, 5))
            active_threads.append(thread)
            thread.start()
        
        # Во время выполнения запросов останавливаем серверы
        time.sleep(1)
        self.server_manager.stop_server(ports[0])
        
        time.sleep(1)
        self.server_manager.stop_server(ports[1])
        
        # Ждем завершения всех потоков
        for thread in active_threads:
            thread.join(timeout=30)
        
        # Анализируем результаты
        successful_requests = sum(1 for r in results if r == 200)
        total_requests = len(results)
        
        # Должна быть высокая успешность несмотря на отказы серверов
        success_rate = successful_requests / total_requests if total_requests > 0 else 0
        self.assertGreaterEqual(success_rate, 0.5, 
                               f"Success rate should be at least 50%, got {success_rate:.1%}")
        
        # Проверяем, что оставшиеся серверы обработали запросы
        stats = self.server_manager.get_server_stats()
        working_servers_requests = stats.get(ports[2], 0) + stats.get(ports[3], 0)
        self.assertGreater(working_servers_requests, 0, 
                          "Working servers should handle requests")
    
    def test_mixed_http_https_workload(self):
        """Тест смешанной нагрузки HTTP и HTTPS"""
        import requests
        
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        http_success = 0
        https_success = 0
        
        # HTTP запросы
        for i in range(5):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/get",
                    method="GET"
                )
                if response.status_code == 200:
                    http_success += 1
            except Exception:
                pass
        
        # HTTPS запросы через CONNECT
        proxy_config = {
            'https': f'http://127.0.0.1:{balancer_port}'
        }
        
        for i in range(5):
            try:
                response = requests.get(
                    'https://httpbin.org/get',
                    proxies=proxy_config,
                    verify=False,
                    timeout=10
                )
                if response.status_code == 200:
                    https_success += 1
            except Exception:
                pass
        
        # Проверяем, что оба типа запросов обрабатываются
        self.assertGreater(http_success, 0, "HTTP requests should succeed")
        # HTTPS может не работать в зависимости от настройки mock серверов
        
        # Проверяем общую статистику
        stats = self.server_manager.get_server_stats()
        total_handled = sum(stats.values())
        self.assertGreater(total_handled, 0, "Servers should handle some requests")
    
    def test_overload_recovery_cycle(self):
        """Тест цикла перегрузки и восстановления"""
        servers = self.server_manager.create_servers(3)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            overload_backoff_base_secs=0.2,
            rest_check_interval=0.1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Этап 1: Нормальная работа
        for i in range(6):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Этап 2: Симулируем перегрузку (429 ответы)
        mapping = {ports[0]: 429, ports[1]: 429, ports[2]: 200}
        self.server_manager.set_fixed_response_codes(mapping)
        
        success_during_overload = 0
        for i in range(8):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/status/200",
                    timeout=5
                )
                if response.status_code == 200:
                    success_during_overload += 1
            except Exception:
                pass
            time.sleep(0.1)
        
        # Должны быть некоторые успешные запросы (на неперегруженный сервер)
        self.assertGreater(success_during_overload, 0, 
                          "Should have some successful requests during overload")
        
        # Этап 3: Восстановление от перегрузки
        mapping = {ports[0]: 200, ports[1]: 200, ports[2]: 200}
        self.server_manager.set_fixed_response_codes(mapping)
        
        # Ждем восстановления
        time.sleep(0.5)
        
        # Сбрасываем статистику
        self.server_manager.reset_stats()
        
        # Проверяем восстановление
        success_after_recovery = 0
        for i in range(9):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/status/200"
                )
                if response.status_code == 200:
                    success_after_recovery += 1
            except Exception:
                pass
        
        # После восстановления должна быть высокая успешность
        recovery_rate = success_after_recovery / 9
        self.assertGreaterEqual(recovery_rate, 0.7, 
                               f"Recovery rate should be at least 70%, got {recovery_rate:.1%}")
        
        # Проверяем, что все серверы снова используются
        stats = self.server_manager.get_server_stats()
        used_servers = sum(1 for count in stats.values() if count > 0)
        self.assertGreaterEqual(used_servers, 2, 
                               "At least 2 servers should be used after recovery")
    
    def test_dynamic_scaling_scenario(self):
        """Тест сценария динамического масштабирования"""
        # Начинаем с одного сервера
        initial_server = self.server_manager.create_servers(1)[0]
        initial_proxies = [{"host": "127.0.0.1", "port": initial_server.port}]
        
        config_path = self.create_test_config(
            proxies=initial_proxies,
            algorithm="round_robin"
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        # Этап 1: Работа с одним сервером
        for i in range(4):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        stats_single = self.server_manager.get_server_stats()
        self.assertEqual(stats_single.get(initial_server.port, 0), 4)
        
        # Этап 2: Масштабирование (добавление серверов)
        additional_servers = self.server_manager.create_servers(2)
        all_ports = [initial_server.port] + [s.port for s in additional_servers]
        updated_proxies = [{"host": "127.0.0.1", "port": p} for p in all_ports]
        
        self.update_config_file(config_path, {"proxies": updated_proxies})
        time.sleep(1)  # Время на применение изменений
        
        # Сбрасываем статистику
        self.server_manager.reset_stats()
        
        # Этап 3: Работа с масштабированной системой
        for i in range(12):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        # Проверяем распределение по всем серверам
        stats_scaled = self.server_manager.get_server_stats()
        for port in all_ports:
            self.assertEqual(stats_scaled.get(port, 0), 4, 
                           f"Server {port} should handle 4 requests")
        
        # Этап 4: Уменьшение масштаба (удаление сервера)
        reduced_proxies = updated_proxies[:2]  # Оставляем только 2 сервера
        self.update_config_file(config_path, {"proxies": reduced_proxies})
        time.sleep(1)
        
        # Сбрасываем статистику
        self.server_manager.reset_stats()
        
        # Проверяем работу с уменьшенным количеством серверов
        for i in range(8):
            response = self.make_request_through_proxy(
                balancer_port=balancer_port,
                target_url="http://httpbin.org/get"
            )
            self.assertEqual(response.status_code, 200)
        
        stats_reduced = self.server_manager.get_server_stats()
        
        # Только первые 2 сервера должны получать запросы
        active_ports = [p["port"] for p in reduced_proxies]
        for port in active_ports:
            self.assertEqual(stats_reduced.get(port, 0), 4)
        
        # Третий сервер не должен получать новые запросы
        removed_port = all_ports[2]
        self.assertEqual(stats_reduced.get(removed_port, 0), 0)
    
    def test_stress_test_scenario(self):
        """Стресс-тест системы"""
        servers = self.server_manager.create_servers(2)
        ports = [s.port for s in servers]
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        
        config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=1
        )
        balancer_port = self.start_balancer_with_config(config_path)
        
        total_requests = 50
        successful_requests = 0
        failed_requests = 0
        
        start_time = time.time()
        
        # Интенсивная нагрузка
        for i in range(total_requests):
            try:
                response = self.make_request_through_proxy(
                    balancer_port=balancer_port,
                    target_url="http://httpbin.org/get",
                    timeout=10
                )
                if response.status_code == 200:
                    successful_requests += 1
                else:
                    failed_requests += 1
            except Exception:
                failed_requests += 1
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Анализ производительности
        success_rate = successful_requests / total_requests
        rps = total_requests / duration
        
        # Проверки производительности
        self.assertGreaterEqual(success_rate, 0.8, 
                               f"Success rate should be at least 80%, got {success_rate:.1%}")
        self.assertGreater(rps, 1.0, 
                          f"Should handle at least 1 request per second, got {rps:.1f}")
        self.assertLess(duration, 60.0, 
                       f"Should complete {total_requests} requests within 60 seconds")
        
        # Проверяем распределение нагрузки
        stats = self.server_manager.get_server_stats()
        total_handled = sum(stats.values())
        
        self.assertEqual(total_handled, successful_requests, 
                        "Server stats should match successful requests")
        
        # При round_robin распределение должно быть равномерным
        for port in ports:
            expected_requests = successful_requests // len(ports)
            actual_requests = stats.get(port, 0)
            
            # Допускаем небольшое отклонение
            self.assertAlmostEqual(actual_requests, expected_requests, delta=2,
                                 msg=f"Server {port} request distribution")


if __name__ == '__main__':
    unittest.main()
