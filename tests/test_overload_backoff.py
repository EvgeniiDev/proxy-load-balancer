import unittest

from tests.base_test import BaseLoadBalancerTest


class TestOverloadBackoff(BaseLoadBalancerTest):
    def setUp(self):
        super().setUp()
        servers = self.server_manager.create_servers(2)
        self.p1, self.p2 = servers[0].port, servers[1].port
        proxies = [
            {"host": "127.0.0.1", "port": self.p1},
            {"host": "127.0.0.1", "port": self.p2},
        ]
        self.config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=1,
            connection_timeout=3,
            max_retries=1,
        )
        self.balancer_port = self.start_balancer_with_config(self.config_path)

    def test_429_triggers_rest_and_switch(self):
        self.update_config_file(
            self.config_path,
            {"overload_backoff_base_secs": 0.2, "rest_check_interval": 0.05},
        )
        r1 = self.make_request_through_proxy(
            balancer_port=self.balancer_port, target_url="http://httpbin.org/status/429"
        )
        self.assertEqual(r1.status_code, 429)

        self.wait_for_health_check(0.4)
        r2 = self.make_request_through_proxy(
            balancer_port=self.balancer_port, target_url="http://httpbin.org/status/200"
        )
        self.assertEqual(r2.status_code, 200)

        stats = self.server_manager.get_server_stats()
        self.assertGreater(stats.get(self.p1, 0), 0)
        self.assertGreater(stats.get(self.p2, 0), 0)

    def test_multiple_requests_no_429_after_backoff(self):
        self.update_config_file(
            self.config_path,
            {"overload_backoff_base_secs": 0.2, "rest_check_interval": 0.05},
        )
        r1 = self.make_request_through_proxy(
            balancer_port=self.balancer_port, target_url="http://httpbin.org/status/429"
        )
        self.assertEqual(r1.status_code, 429)

        self.wait_for_health_check(0.3)

        for _ in range(3):
            r = self.make_request_through_proxy(
                balancer_port=self.balancer_port, target_url="http://httpbin.org/status/200"
            )
            self.assertNotEqual(r.status_code, 429)
