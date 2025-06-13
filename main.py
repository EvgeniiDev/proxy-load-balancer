import argparse
import sys
import time

from balancer import ProxyBalancer
from balancer.config import load_config


def start_balancer(config_file: str, verbose: bool = False):
    try:
        config = load_config(config_file)
        balancer = ProxyBalancer(config)

        print(
            f"Starting proxy balancer on {config['server']['host']}:{config['server']['port']}")
        print(f"Proxies: {len(config['proxies'])}")
        if verbose:
            print("Verbose mode enabled")

        balancer.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
            balancer.stop()
            print("Stopped")

    except FileNotFoundError:
        print(f"Config file not found: {config_file}")
    except Exception as e:
        print(f"Error: {e}")


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


def main():
    parser = argparse.ArgumentParser(description="HTTP Proxy Load Balancer")
    parser.add_argument("-c", "--config", default="config.json",
                        help="Configuration file path")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose output")

    args = parser.parse_args()

    return start_balancer(args.config, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
