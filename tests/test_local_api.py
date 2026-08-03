import json
import unittest
import urllib.request

from agent.local_api import start_local_api


class FakeDevice:
    def state(self):
        return {"connected": True, "serial": "test-device"}


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        self.server = start_local_api("127.0.0.1", 0, FakeDevice())
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def get_json(self, path: str):
        with urllib.request.urlopen(self.base_url + path, timeout=2) as response:
            return response.status, json.load(response)

    def test_health_endpoint(self):
        status, payload = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_device_endpoint_uses_device_directly(self):
        status, payload = self.get_json("/api/device")
        self.assertEqual(status, 200)
        self.assertEqual(payload["serial"], "test-device")


if __name__ == "__main__":
    unittest.main()
