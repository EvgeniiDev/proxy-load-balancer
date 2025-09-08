import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .base import ConfigValidator, Logger


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
            # Reduce debounce to allow rapid successive updates during tests
            if current_time - self.last_modified > 0.1:
                self.last_modified = current_time
                thread = threading.Thread(target=self._reload_config)
                thread.daemon = True
                thread.start()

    def _reload_config(self):
        try:
            new_config = load_config(str(self.config_file))
            if ConfigValidator.validate_config(new_config):
                self.callback(new_config)
            else:
                print(f"Invalid configuration in {self.config_file}, ignoring changes")
        except Exception as e:
            print(f"Error reloading config: {e}")


class ConfigManager:
    def __init__(self, config_file: str = "config.json"):
        self.logger = Logger.get_logger("config_manager")
        self.config_file = config_file
        self.config = load_config(config_file)
        self.observer = None
        self.callbacks: list[Any] = []

    def add_change_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Добавление callback для изменения конфигурации."""
        self.callbacks.append(callback)

    def start_monitoring(self):
        """Запуск мониторинга изменений конфигурации."""
        if self.observer is not None:
            return
        config_path = Path(self.config_file)
        watch_dir = config_path.parent
        self.observer = Observer()
        handler = ConfigHandler(self.config_file, self._on_config_changed)
        self.observer.schedule(handler, str(watch_dir), recursive=False)
        self.observer.start()
        self.logger.info(f"Started monitoring config file: {self.config_file}")

    def stop_monitoring(self):
        """Остановка мониторинга изменений конфигурации."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            self.logger.info("Stopped monitoring config file")

    def _on_config_changed(self, new_config: Dict[str, Any]):
        """Обработка изменения конфигурации."""
        self.config = new_config
        for callback in self.callbacks:
            try:
                callback(new_config)
            except Exception as e:
                self.logger.error(f"Error in config change callback: {e}")

    def get_config(self) -> Dict[str, Any]:
        """Получение копии текущей конфигурации."""
        return self.config.copy()
    
    def reload_config(self):
        """Ручная перезагрузка конфигурации."""
        try:
            new_config = load_config(self.config_file)
            self._on_config_changed(new_config)
        except Exception as e:
            self.logger.error(f"Error reloading config manually: {e}")


def load_config(config_file: str = "config.json") -> Dict[str, Any]:
    """Загрузка конфигурации из файла."""
    with open(config_file, "r", encoding='utf-8') as f:
        return json.load(f)
