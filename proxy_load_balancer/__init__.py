from .proxy_balancer import ProxyBalancer
from .proxy_stats import ProxyStats
from .stats_reporter import StatsReporter
from .base import ProxyHandler, ConfigValidator, Logger
from .config import ConfigManager
import threading

def run_balancer_daemon(config_file: str = "config.json", verbose: bool = False):
    config_manager = ConfigManager(config_file)
    config = config_manager.get_config()
    balancer = ProxyBalancer(config)
    def on_config_change(new_config):
        if verbose:
            print("Configuration changed, updating balancer...")
        balancer.update_proxies(new_config)
        balancer.reload_algorithm()
    config_manager.add_change_callback(on_config_change)
    config_manager.start_monitoring()
    if verbose:
        print(f"Starting proxy balancer")
        print(f"Proxies: {len(config['proxies'])}")
        print(f"Config monitoring: enabled for {config_file}")
        print("Verbose mode enabled")
    balancer.start()
    main_thread_event = threading.Event()
    try:
        main_thread_event.wait()
    except KeyboardInterrupt:
        config_manager.stop_monitoring()
        balancer.stop()

__all__ = ["ProxyBalancer", "StatsReporter", "run_balancer_daemon"]
