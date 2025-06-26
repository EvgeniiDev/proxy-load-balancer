import time
import logging
from typing import Dict, List, Any
from .utils import ProxyManager


class StatsReporter:
    def __init__(self, proxy_balancer):
        self.proxy_balancer = proxy_balancer
        self.logger = logging.getLogger("stats_reporter")

    def get_stats(self) -> Dict[str, Any]:
        with self.proxy_balancer.stats_lock:
            total_requests = sum(stats.request_count for stats in self.proxy_balancer.proxy_stats.values())
            total_successes = sum(stats.success_count for stats in self.proxy_balancer.proxy_stats.values())
            total_failures = sum(stats.failure_count for stats in self.proxy_balancer.proxy_stats.values())
            
            proxy_stats = {}
            
            for key, stats in self.proxy_balancer.proxy_stats.items():
                proxy_stats[key] = {
                    "requests": stats.request_count,
                    "successes": stats.success_count,
                    "failures": stats.failure_count,
                    "success_rate": round(stats.get_success_rate(), 2),
                    "status": "available" if any(ProxyManager.get_proxy_key(p) == key for p in self.proxy_balancer.available_proxies) else "unavailable",
                    "sessions_pooled": len(stats.session_pool)
                }
            
            return {
                "total_requests": total_requests,
                "total_successes": total_successes,
                "total_failures": total_failures,
                "overall_success_rate": round((total_successes / (total_successes + total_failures) * 100) if (total_successes + total_failures) > 0 else 0, 2),
                "available_proxies_count": len(self.proxy_balancer.available_proxies),
                "unavailable_proxies_count": len(self.proxy_balancer.unavailable_proxies),
                "algorithm": type(self.proxy_balancer.load_balancer).__name__,
                "proxy_stats": proxy_stats
            }

    def print_stats(self) -> None:
        stats = self.get_stats()
        
        print("\n" + "="*60)
        print("PROXY LOAD BALANCER STATISTICS")
        print("="*60)
        print(f"Algorithm: {stats['algorithm']}")
        print(f"Total Requests: {stats['total_requests']}")
        print(f"Total Successes: {stats['total_successes']}")
        print(f"Total Failures: {stats['total_failures']}")
        print(f"Overall Success Rate: {stats['overall_success_rate']}%")
        print(f"Available Proxies: {stats['available_proxies_count']}")
        print(f"Unavailable Proxies: {stats['unavailable_proxies_count']}")
        
        print("\nPER-PROXY STATISTICS:")
        print("-" * 60)
        print(f"{'Proxy':<20} {'Requests':<10} {'Success':<10} {'Failures':<10} {'Rate':<8} {'Status':<12}")
        print("-" * 60)
        
        for proxy_key, proxy_stats in stats['proxy_stats'].items():
            print(f"{proxy_key:<20} {proxy_stats['requests']:<10} {proxy_stats['successes']:<10} "
                  f"{proxy_stats['failures']:<10} {proxy_stats['success_rate']:<7}% {proxy_stats['status']:<12}")
        
        print("="*60)

    def print_compact_stats(self) -> None:
        if not self.proxy_balancer.verbose:
            return
            
        stats = self.get_stats()
        
        print(f"[{time.strftime('%H:%M:%S')}] Stats: {stats['total_requests']} reqs, "
              f"{stats['overall_success_rate']}% success, "
              f"{stats['available_proxies_count']}/{stats['available_proxies_count'] + stats['unavailable_proxies_count']} proxies up | ", end="")
        
        proxy_summaries = []
        for proxy_key, proxy_stats in stats['proxy_stats'].items():
            if proxy_stats['requests'] > 0:
                status_symbol = "✓" if proxy_stats['status'] == "available" else "✗"
                proxy_summaries.append(f"{proxy_key}({proxy_stats['requests']}r/{proxy_stats['success_rate']}%{status_symbol})")
        
        print(" | ".join(proxy_summaries))

    def log_stats_summary(self) -> None:
        stats = self.get_stats()
        self.logger.info(f"Stats Summary - Requests: {stats['total_requests']}, "
                        f"Success Rate: {stats['overall_success_rate']}%, "
                        f"Available Proxies: {stats['available_proxies_count']}/{stats['available_proxies_count'] + stats['unavailable_proxies_count']}")
