import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class ConfigHandler(FileSystemEventHandler):
    def __init__(self, config_file: str, callback: Callable[[Dict[str, Any]], None]):
        self.config_file = Path(config_file).resolve()
        self.callback = callback
        self.last_modified = 0

    def on_modified(self, event):
        if event.is_directory:
            return
        if Path(str(event.src_path)).resolve() == self.config_file:
            current_time = time.time()
            if current_time - self.last_modified > 1:
                self.last_modified = current_time
                try:
                    new_config = load_config(str(self.config_file))
                    if self.validate_config(new_config):
                        self.callback(new_config)
                        print(f"Configuration reloaded from {self.config_file}")
                    else:
                        print(f"Invalid configuration in {self.config_file}, ignoring changes")
                except Exception as e:
                    print(f"Error reloading config: {e}")

    def validate_config(self, config: Dict[str, Any]) -> bool:
        required_fields = ["server", "proxies", "health_check_interval", "max_retries"]

        for field in required_fields:
            if field not in config:
                print(f"Missing required field in config: {field}")
                return False

        if not isinstance(config["proxies"], list):
            print("Proxies must be a list")
            return False

        for i, proxy in enumerate(config["proxies"]):
            if not isinstance(proxy, dict) or "host" not in proxy or "port" not in proxy:
                print(f"Invalid proxy configuration at index {i}")
                return False

        return True


class ConfigManager:
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.config = load_config(config_file)
        self.observer = None
        self.callbacks = []

    def add_change_callback(self, callback: Callable[[Dict[str, Any]], None]):
        self.callbacks.append(callback)

    def start_monitoring(self):
        if self.observer is not None:
            return

        config_path = Path(self.config_file)
        watch_dir = config_path.parent

        self.observer = Observer()
        handler = ConfigHandler(self.config_file, self._on_config_changed)
        self.observer.schedule(handler, str(watch_dir), recursive=False)
        self.observer.start()
        print(f"Started monitoring config file: {self.config_file}")

    def stop_monitoring(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            print("Stopped monitoring config file")

    def _on_config_changed(self, new_config: Dict[str, Any]):
        self.config = new_config

        for callback in self.callbacks:
            try:
                callback(new_config)
            except Exception as e:
                print(f"Error in config change callback: {e}")

    def get_config(self) -> Dict[str, Any]:
        return self.config.copy()

    def reload_config(self):
        try:
            new_config = load_config(self.config_file)
            self._on_config_changed(new_config)
        except Exception as e:
            print(f"Error reloading config manually: {e}")


def load_config(config_file: str = "config.json") -> Dict[str, Any]:
    with open(config_file, "r", encoding='utf-8') as f:
        return json.load(f)
