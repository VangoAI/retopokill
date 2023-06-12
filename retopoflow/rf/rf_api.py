import requests

class RetopoFlow_API:
    BACKEND_URL = 'http://127.0.0.1:5000'

    @staticmethod
    def post(path: str, args: str | dict | list):
        e = RetopoFlow_API.BACKEND_URL + path
        req_args = {"args": args}
        return requests.post(e, json=req_args)
