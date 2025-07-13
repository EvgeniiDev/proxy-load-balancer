import argparse
import sys
import threading
from proxy_load_balancer.proxy_balancer import ProxyBalancer
from proxy_load_balancer.config import ConfigManager


def display_help():
    print("HTTP Proxy Load Balancer")
    print("")
    print("Usage:")
    print("    python main.py [-c config.json] [-v]")
    print("")
    print("Options:")
    print("    -c, --config    Configuration file path")
    print("    -v, --verbose   Enable verbose output")
    print("    -h, --help      Show this help message")


def start_balancer(config_file: str, verbose: bool = False):
    try:
        config_manager = ConfigManager(config_file)
        config = config_manager.get_config()
        balancer = ProxyBalancer(config, verbose=verbose)

        def on_config_change(new_config):
            if verbose:
                print("Configuration changed, updating balancer...")
            balancer.update_proxies(new_config)
            balancer.reload_algorithm()

        balancer.set_config_manager(config_manager, on_config_change)

        config_manager.add_change_callback(on_config_change)

        config_manager.start_monitoring()

        print(f"Starting proxy balancer on {config['server']['host']}:{config['server']['port']}")
        print(f"Proxies: {len(config['proxies'])}")
        print(f"Config monitoring: enabled for {config_file}")
        if verbose:
            print("Verbose mode enabled")

        balancer.start()

        try:
            # Use a proper event instead of threading.Event().wait()
            shutdown_event = threading.Event()
            
            def signal_handler(signum, frame):
                print("\nReceived signal, shutting down...")
                shutdown_event.set()
            
            import signal
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
            
            shutdown_event.wait()
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            print("Stopping services...")
            config_manager.stop_monitoring()
            balancer.stop()
            print("Stopped")

    except FileNotFoundError:
        print(f"Config file not found: {config_file}")
    except Exception as e:
        print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="HTTP Proxy Load Balancer", add_help=False)
    parser.add_argument("-c", "--config", default="config.json", help="Configuration file path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-h", "--help", action="store_true", help="Show this help message")
    args = parser.parse_args()
    if args.help:
        display_help()
        return 0
    return start_balancer(args.config, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
