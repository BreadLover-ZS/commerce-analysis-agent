"""Explicit pytest plugin: permit loopback/Unix sockets, reject external DNS/connect."""

import json
import os
import socket
from pathlib import Path

ATTEMPTS = []
LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


def pytest_configure(config):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def check(host):
        if host is not None and host not in LOCAL:
            ATTEMPTS.append(str(host))
            raise AssertionError("Local validation forbids external DNS/network")

    def connect(sock, address):
        if isinstance(address, tuple):
            check(address[0])
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if isinstance(address, tuple):
            check(address[0])
        return original_connect_ex(sock, address)

    def resolve(host, *args, **kwargs):
        check(host.decode() if isinstance(host, bytes) else host)
        return original_resolve(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.getaddrinfo = resolve


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.getplugin("terminalreporter")
    if reporter:
        reporter.write_sep(
            "-", f"external network attempts blocked: {len(ATTEMPTS)}; provider sends: 0"
        )
    target = os.environ.get("NO_PROVIDER_AUDIT")
    if target:
        with Path(target).open("x") as stream:
            json.dump(
                {
                    "external_attempts_blocked": len(ATTEMPTS),
                    "hosts": ATTEMPTS,
                    "provider_requests": 0,
                    "loopback_and_unix_sockets_permitted": True,
                },
                stream,
                indent=2,
            )
            stream.write("\n")
