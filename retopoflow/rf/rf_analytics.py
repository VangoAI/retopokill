from enum import Enum
from .rf_api import RetopoFlow_API

class RetopoFlow_Analytics:
    def log_event(self, event: 'Event'):
        res = RetopoFlow_API.post('/log_event', event.to_string())
        if not res.json()['success']:
            print("log failed")
            raise Exception(res.json()['error'])

class Event(Enum):
    START = 1
    END = 2

    def to_string(self):
        return self.name.lower()
