import unittest
import json
import requests
from tests.base_test import BaseLoadBalancerTest


class TestHttpMethods(BaseLoadBalancerTest):
    """Тесты HTTP/HTTPS методов и протоколов"""
    
    def setUp(self):
        super().setUp()
        self.server = self.server_manager.create_servers(1)[0]
        self.proxies = [{"host": "127.0.0.1", "port": self.server.port}]
        self.config_path = self.create_test_config(proxies=self.proxies)
        self.balancer_port = self.start_balancer_with_config(self.config_path)
    
    def test_http_get_method(self):
        """Тест HTTP GET запроса"""
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/get",
            method="GET"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["url"], "http://httpbin.org/get")
        self.assertIn("headers", data)
    
    def test_http_post_method(self):
        """Тест HTTP POST запроса"""
        post_data = {"key": "value", "test": "data"}
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/post",
            method="POST",
            data=json.dumps(post_data),
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(response_data["json"], post_data)
        self.assertEqual(response_data["url"], "http://httpbin.org/post")
    
    def test_http_put_method(self):
        """Тест HTTP PUT запроса"""
        put_data = {"updated": "value", "test": "modified"}
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/put",
            method="PUT",
            data=json.dumps(put_data),
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(response_data["json"], put_data)
        self.assertEqual(response_data["url"], "http://httpbin.org/put")
    
    def test_http_delete_method(self):
        """Тест HTTP DELETE запроса"""
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/delete",
            method="DELETE"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["url"], "http://httpbin.org/delete")
    
    def test_http_head_method(self):
        """Тест HTTP HEAD запроса"""
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/get",
            method="HEAD"
        )
        self.assertEqual(response.status_code, 200)
        # HEAD запросы не должны возвращать тело ответа
        self.assertEqual(len(response.content), 0)
    
    def test_http_patch_method(self):
        """Тест HTTP PATCH запроса"""
        patch_data = {"field": "patched_value"}
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/patch",
            method="PATCH",
            data=json.dumps(patch_data),
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(response_data["json"], patch_data)
    
    def test_https_get_with_ssl_termination(self):
        """Тест HTTPS GET запроса с SSL termination"""
        # Используем HTTPS через CONNECT метод
        proxies = {
            'https': f'http://127.0.0.1:{self.balancer_port}'
        }
        
        response = requests.get(
            'https://httpbin.org/get',
            proxies=proxies,
            verify=False,
            timeout=10
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['url'], 'https://httpbin.org/get')
    
    def test_https_post_with_ssl_termination(self):
        """Тест HTTPS POST запроса с SSL termination"""
        proxies = {
            'https': f'http://127.0.0.1:{self.balancer_port}'
        }
        
        post_data = {"https": "test", "ssl": "termination"}
        
        response = requests.post(
            'https://httpbin.org/post',
            json=post_data,
            proxies=proxies,
            verify=False,
            timeout=10
        )
        
        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(response_data['json'], post_data)
        self.assertEqual(response_data['url'], 'https://httpbin.org/post')
    
    def test_http_with_custom_headers(self):
        """Тест HTTP запроса с пользовательскими заголовками"""
        custom_headers = {
            "X-Custom-Header": "test-value",
            "Authorization": "Bearer token123",
            "User-Agent": "TestClient/1.0"
        }
        
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/headers",
            method="GET",
            headers=custom_headers
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Проверяем, что заголовки были переданы (заголовки могут быть в нижнем регистре)
        received_headers = data["headers"]
        # Convert headers to case-insensitive lookup
        headers_lower = {k.lower(): v for k, v in received_headers.items()}
        self.assertIn("x-custom-header", headers_lower)
        self.assertEqual(headers_lower["x-custom-header"], "test-value")
        self.assertIn("authorization", headers_lower)
        self.assertEqual(headers_lower["authorization"], "Bearer token123")
    
    def test_http_with_query_parameters(self):
        """Тест HTTP запроса с параметрами запроса"""
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/get?param1=value1&param2=value2",
            method="GET"
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        # Проверяем параметры запроса
        args = data["args"]
        self.assertEqual(args["param1"], "value1")
        self.assertEqual(args["param2"], "value2")
    
    def test_http_large_payload(self):
        """Тест HTTP запроса с большим payload"""
        # Создаем большие данные (около 1MB)
        large_data = {"data": "x" * 1000000}
        
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/post",
            method="POST",
            data=json.dumps(large_data),
            headers={"Content-Type": "application/json"}
        )
        
        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(response_data["json"], large_data)
    
    def test_http_response_status_codes(self):
        """Тест различных HTTP статус кодов"""
        status_codes = [200, 201, 204, 400, 404, 500]
        
        for status_code in status_codes:
            with self.subTest(status_code=status_code):
                response = self.make_request_through_proxy(
                    balancer_port=self.balancer_port,
                    target_url=f"http://httpbin.org/status/{status_code}",
                    method="GET"
                )
                self.assertEqual(response.status_code, status_code)
    
    def test_http_redirect_handling(self):
        """Тест обработки HTTP редиректов"""
        # httpbin возвращает редирект на /get
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/redirect/1",
            method="GET"
        )
        
        # requests автоматически следует редиректам
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["url"], "http://httpbin.org/get")
    
    def test_http_timeout_handling(self):
        """Тест обработки таймаутов HTTP"""
        # httpbin/delay/10 задерживает ответ на 10 секунд
        with self.assertRaises((requests.exceptions.Timeout, requests.exceptions.RequestException)):
            self.make_request_through_proxy(
                balancer_port=self.balancer_port,
                target_url="http://httpbin.org/delay/10",
                method="GET",
                timeout=2  # Короткий таймаут
            )
    
    def test_content_encoding_support(self):
        """Тест поддержки сжатия контента"""
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/gzip",
            method="GET",
            headers={"Accept-Encoding": "gzip"}
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["gzipped"])
    
    def test_multiple_content_types(self):
        """Тест различных типов контента"""
        content_types = [
            ("application/json", {"json": "data"}),
            ("text/plain", "plain text data"),
            ("application/xml", "<xml><data>test</data></xml>")
        ]
        
        for content_type, data in content_types:
            with self.subTest(content_type=content_type):
                if isinstance(data, dict):
                    payload = json.dumps(data)
                else:
                    payload = data
                
                response = self.make_request_through_proxy(
                    balancer_port=self.balancer_port,
                    target_url="http://httpbin.org/post",
                    method="POST",
                    data=payload,
                    headers={"Content-Type": content_type}
                )
                
                self.assertEqual(response.status_code, 200)
    
    def test_http_connection_reuse(self):
        """Тест переиспользования HTTP соединений"""
        # Делаем несколько запросов подряд к одному хосту
        for i in range(5):
            response = self.make_request_through_proxy(
                balancer_port=self.balancer_port,
                target_url=f"http://httpbin.org/get?request={i}",
                method="GET"
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["args"]["request"], str(i))
        
        # Проверяем, что все запросы прошли через один прокси
        stats = self.server_manager.get_server_stats()
        self.assertEqual(stats.get(self.server.port, 0), 5)


if __name__ == '__main__':
    unittest.main()
