import unittest

from tests.base_test import BaseLoadBalancerTest


class TestMixedProxiesSwitch(BaseLoadBalancerTest):
    def setUp(self):
        super().setUp()
        servers = self.server_manager.create_servers(4)
        ports = [s.port for s in servers]
        mapping = {ports[0]: 429, ports[1]: 429, ports[2]: 200, ports[3]: 200}
        self.server_manager.set_fixed_response_codes(mapping)
        proxies = [{"host": "127.0.0.1", "port": p} for p in ports]
        self.config_path = self.create_test_config(
            proxies=proxies,
            algorithm="round_robin",
            health_check_interval=1,
            connection_timeout=1,
            max_retries=1,
        )
        self.balancer_port = self.start_balancer_with_config(self.config_path, wait_for_start=1.5)

    def test_balancer_always_returns_200_by_switching(self):
        r = self.make_request_through_proxy(
            balancer_port=self.balancer_port, target_url="http://example.com/resource"
        )
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
