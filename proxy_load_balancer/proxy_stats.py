from typing import List, Optional
import requests


class ProxyStats:
    def __init__(self):
        self.request_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.total_failures = 0
        self.overload_count = 0
        self.total_overloads = 0
        self.total_429 = 0
        self.responses_200 = 0
        self.responses_429 = 0
        self.responses_other = 0
        self.session_pool = []

    def increment_requests(self):
        self.request_count += 1
    
    def increment_successes(self):
        self.success_count += 1
        self.failure_count = 0
    
    def increment_failures(self):
        self.failure_count += 1
        self.total_failures += 1
    
    def increment_overloads(self):
        """Увеличивает счетчик перегрузок"""
        self.overload_count += 1
        self.total_overloads += 1
    
    def reset_overload_count(self):
        """Сбрасывает счетчик текущих перегрузок"""
        self.overload_count = 0

    def increment_429(self):
        self.total_429 += 1
        self.responses_429 += 1
    
    def increment_200(self):
        self.responses_200 += 1
    
    def increment_other(self):
        self.responses_other += 1
    
    def get_success_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return (self.success_count / self.request_count) * 100
    
    def add_session(self, session: requests.Session, max_pool_size: int = 5):
        if len(self.session_pool) < max_pool_size:
            self.session_pool.append(session)
            return True
        else:
            session.close()
            return False
    
    def get_session(self) -> Optional[requests.Session]:
        if self.session_pool:
            return self.session_pool.pop()
        return None
    
    def close_all_sessions(self):
        for session in self.session_pool:
            session.close()
        self.session_pool.clear()
