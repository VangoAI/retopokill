import requests
from enum import Enum
from ...config.options import options

class RetopoFlow_Analytics:
    def __init__(self):
        with open(options['uuid_filename'], 'r') as f:
            self.uuid = f.read()
        self.log_event(Event.START)

    def log_event(self, event):
        res = requests.post(options.get_endpoint('/log_event'), json={'uuid': self.uuid, 'event': event.to_string()})
        if not res.json()['success']:
            print("log failed")

class Event(Enum):
    START = 1

    def to_string(self):
        return self.name.lower()