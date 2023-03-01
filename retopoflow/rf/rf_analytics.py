import requests
from enum import Enum
from ...config.options import options

class RetopoFlow_Analytics:
    def log_event(self, event):
        res = options.make_post_request('/log_event', args = event.to_string())
        if not res.json()['success']:
            print("log failed")

class Event(Enum):
    START = 1
    END = 2

    def to_string(self):
        return self.name.lower()