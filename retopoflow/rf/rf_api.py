import requests

class RetopoFlow_API:
    BACKEND_URL = 'http://35.90.250.174:5000'
    API_KEY = ''

    @staticmethod
    def post(path: str, args: str | dict | list):
        assert RetopoFlow_API.API_KEY, "API key not set"

        e = RetopoFlow_API.BACKEND_URL + path
        req_args = {"key": RetopoFlow_API.API_KEY, "args": args}
        return requests.post(e, json=req_args)
    
    @staticmethod
    def set_api_key(api_key: str):
        RetopoFlow_API.API_KEY = api_key
