"""Bounded HTTPS with pinned public DNS addresses and no redirects."""

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

MAX_BODY = 262_144


def public_addresses(host):
    addresses = sorted(
        {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    )
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Provider DNS must resolve exclusively to public addresses")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection((self.pinned_address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def request(endpoint, headers=None, *, max_body=MAX_BODY):
    if type(max_body) is not int or not 1 <= max_body <= 1_048_576:
        raise ValueError("Invalid provider response bound")
    url = urlsplit(endpoint)
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.fragment
        or url.port not in (None, 443)
    ):
        raise ValueError("Unsupported provider URL")
    connection = PinnedHTTPS(
        url.hostname, timeout=12, context=ssl.create_default_context()
    )
    connection.pinned_address = public_addresses(url.hostname)[0]
    try:
        connection.request(
            "GET",
            (url.path or "/") + ("?" + url.query if url.query else ""),
            headers={
                "Accept": "application/json",
                "User-Agent": "Nuria-commerce/0.5",
                **(headers or {}),
            },
        )
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise ValueError("Provider redirects are disabled")
        raw = response.read(max_body + 1)
        if len(raw) > max_body:
            raise ValueError("Provider response exceeds size limit")
        return (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            raw,
        )
    finally:
        connection.close()
