import random
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class LoadBalancingAlgorithm(ABC):
    def __init__(self):
        self.lock = threading.Lock()

    @abstractmethod
    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def reset(self) -> None:
        pass


class RandomAlgorithm(LoadBalancingAlgorithm):
    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not available_proxies:
            return None
        return random.choice(available_proxies)

    def reset(self) -> None:
        pass


class RoundRobinAlgorithm(LoadBalancingAlgorithm):
    def __init__(self):
        super().__init__()
        self.current_index = 0

    def select_proxy(self, available_proxies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not available_proxies:
            return None

        with self.lock:
            if self.current_index >= len(available_proxies):
                self.current_index = 0

            selected_proxy = available_proxies[self.current_index]
            self.current_index = (self.current_index + 1) % len(available_proxies)

            return selected_proxy

    def reset(self) -> None:
        with self.lock:
            self.current_index = 0


class AlgorithmFactory:
    _algorithms = {
        'random': RandomAlgorithm,
        'round_robin': RoundRobinAlgorithm,
    }

    @classmethod
    def create_algorithm(cls, algorithm_name: str) -> LoadBalancingAlgorithm:
        algorithm_name_lower = algorithm_name.lower()

        if algorithm_name_lower not in cls._algorithms:
            available = ', '.join(cls._algorithms.keys())
            raise ValueError(
                f"Неизвестный алгоритм балансировки: {algorithm_name}. "
                f"Доступные алгоритмы: {available}"
            )

        return cls._algorithms[algorithm_name_lower]()

    @classmethod
    def get_available_algorithms(cls) -> List[str]:
        return list(cls._algorithms.keys())
