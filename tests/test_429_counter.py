import unittest
import time
import requests
from tests.base_test import BaseLoadBalancerTest


class Test429Counter(BaseLoadBalancerTest):
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
            stats_interval=1,
        )
        self.balancer_port = self.start_balancer_with_config(self.config_path)

    def test_429_counter_increases(self):
        # Make request that should trigger 429
        r1 = self.make_request_through_proxy(
            balancer_port=self.balancer_port, target_url="http://httpbin.org/status/429"
        )
        self.assertEqual(r1.status_code, 503)

        # Wait for stats to be printed
        time.sleep(2)

        # Get raw stats
        raw_stats = self.server_manager.balancer.stats_reporter.get_stats()
        print(f"Raw stats: {raw_stats}")

        # Check that total_429 > 0
        self.assertGreater(raw_stats['total_429'], 0, "429 counter should be greater than 0")


if __name__ == '__main__':
    unittest.main()
