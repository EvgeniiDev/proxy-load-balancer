import unittest
import json
from tests.base_test import BaseLoadBalancerTest

class TestAllMethods(BaseLoadBalancerTest):
    def setUp(self):
        super().setUp()
        self.server = self.server_manager.create_servers(1)[0]
        self.proxies = [{"host": "127.0.0.1", "port": self.server.port}]
        self.config_path = self.create_test_config(proxies=self.proxies)
        self.balancer_port = self.start_balancer_with_config(self.config_path)

    def test_http_get(self):
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/get",
            method="GET"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "http://httpbin.org/get")

    def test_https_get(self):
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="https://httpbin.org/get",
            method="GET"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "https://httpbin.org/get")

    def test_http_post(self):
        post_data = {"test": "data"}
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/post",
            method="POST",
            data=json.dumps(post_data),
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["json"], post_data)

    def test_https_post(self):
        post_data = {"test": "data"}
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="https://httpbin.org/post",
            method="POST",
            data=json.dumps(post_data),
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["json"], post_data)

    def test_http_put(self):
        put_data = {"test": "updated"}
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/put",
            method="PUT",
            data=json.dumps(put_data),
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["json"], put_data)

    def test_https_put(self):
        put_data = {"test": "updated"}
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="https://httpbin.org/put",
            method="PUT",
            data=json.dumps(put_data),
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["json"], put_data)

    def test_http_delete(self):
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="http://httpbin.org/delete",
            method="DELETE"
        )
        self.assertEqual(response.status_code, 200)

    def test_https_delete(self):
        response = self.make_request_through_proxy(
            balancer_port=self.balancer_port,
            target_url="https://httpbin.org/delete",
            method="DELETE"
        )
        self.assertEqual(response.status_code, 200)

if __name__ == '__main__':
    unittest.main()
