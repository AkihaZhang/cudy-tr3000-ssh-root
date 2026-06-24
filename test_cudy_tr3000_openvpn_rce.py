import hashlib
import io
import unittest
from contextlib import redirect_stderr

from cudy_tr3000_openvpn_rce import read_device_identity


class FakeClient:
    def __init__(self, bind_result=None, bind_error=None, conf=None):
        self.bind_result = bind_result
        self.bind_error = bind_error
        self.conf = conf or {"client": {}}
        self.calls = []

    def app(self, method, params=None):
        self.calls.append(("app", method, params or []))
        if method == "system.bind_token":
            if self.bind_error:
                raise self.bind_error
            return self.bind_result
        if method == "conf.get_all":
            return self.conf
        raise AssertionError(f"unexpected app call: {method}")

    def run_command(self, command):
        self.calls.append(("run_command", command))


class DeviceIdentityTests(unittest.TestCase):
    def test_reads_fuuid_from_bind_token_and_hmac_from_bdinfo(self):
        client = FakeClient(
            bind_result={"fuuid": "FUUID"},
            conf={"client": {"password": "HMAC"}},
        )

        fuuid, hmac, root_password = read_device_identity(client)

        self.assertEqual(fuuid, "FUUID")
        self.assertEqual(hmac, "HMAC")
        self.assertEqual(root_password, hashlib.sha256(b"FUUIDHMAC").hexdigest())
        self.assertIn(
            (
                "run_command",
                "uci set openvpn.client.password=$(bdinfo hmac);uci commit openvpn",
            ),
            client.calls,
        )

    def test_falls_back_to_bdinfo_when_bind_token_is_rejected(self):
        client = FakeClient(
            bind_error=RuntimeError("Invalid Request"),
            conf={"client": {"username": "FUUID", "password": "HMAC"}},
        )

        with redirect_stderr(io.StringIO()):
            fuuid, hmac, root_password = read_device_identity(client)

        self.assertEqual(fuuid, "FUUID")
        self.assertEqual(hmac, "HMAC")
        self.assertEqual(root_password, hashlib.sha256(b"FUUIDHMAC").hexdigest())
        self.assertIn(
            (
                "run_command",
                "uci set openvpn.client.username=$(bdinfo fuuid);"
                "uci set openvpn.client.password=$(bdinfo hmac);"
                "uci commit openvpn",
            ),
            client.calls,
        )


if __name__ == "__main__":
    unittest.main()
