"""Reuse M2-02a's network guard for isolated F04 and original 335 offline tests."""
from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
MODE = sys.argv[1] if len(sys.argv) == 2 else "all"
if MODE not in ("new", "regression", "all"):
    raise SystemExit("usage: verify_m202c_offline.py [new|regression|all]")
OUT = ROOT / "Docs/M2-02c" / MODE


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    counts = dict(dns=0, tcp=0, udp=0, asyncio_local_wakeup_connections=0)
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    wakeup_code = getattr(socket.socketpair, '__code__', None)

    def local_wakeup(address):
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1', 'localhost'):
            return False
        frame = sys._getframe(1)
        while frame:
            if wakeup_code is not None and frame.f_code is wakeup_code:
                return True
            frame = frame.f_back
        return False

    def connect(sock, address):
        if local_wakeup(address):
            counts['asyncio_local_wakeup_connections'] += 1
            return original_connect(sock, address)
        counts['tcp'] += 1
        raise AssertionError('offline guard blocked TCP')

    def connect_ex(sock, address):
        if local_wakeup(address):
            counts['asyncio_local_wakeup_connections'] += 1
            return original_connect_ex(sock, address)
        counts['tcp'] += 1
        raise AssertionError('offline guard blocked TCP')

    def dns(*args, **kwargs):
        counts['dns'] += 1
        raise AssertionError('offline guard blocked DNS')

    def udp(*args, **kwargs):
        counts['udp'] += 1
        raise AssertionError('offline guard blocked UDP')

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.getaddrinfo = socket.gethostbyname = socket.gethostbyname_ex = dns
    socket.socket.sendto = udp
    if hasattr(socket.socket, 'sendmsg'):
        socket.socket.sendmsg = udp
    # Prove the guard rejects before any OS connection/DNS attempt.
    def tcp_self_test():
        with socket.socket() as sock:
            sock.connect(('203.0.113.1', 443))
    def udp_self_test():
        with socket.socket(type=socket.SOCK_DGRAM) as sock:
            sock.sendto(b'guard-test', ('203.0.113.1', 443))
    for invoke in (lambda: socket.getaddrinfo('must-not-resolve.invalid', 443), tcp_self_test, udp_self_test):
        try:
            invoke()
        except AssertionError:
            pass
        else:
            raise AssertionError('offline guard self-test failed')
    self_test = counts.copy()
    status = 1
    try:
        import pytest
        with (OUT / '离线测试原始日志.txt').open('w', encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log):
            print('M2-02c DNS/TCP/UDP deny guards installed before pytest imports; mode=' + MODE)
            print('Guard self-tests: DNS/TCP/UDP blocked before network I/O; asyncio local wakeups exempt')
            targets = ['tests/feasibility/test_transit_feasibility.py'] if MODE == 'new' else ['tests/feasibility/test_opening_feasibility.py', 'tests/feasibility/test_travel_feasibility.py', 'tests/places'] if MODE == 'regression' else ['tests/feasibility', 'tests/places']
            status = pytest.main(targets + ['-v', '-s', '--tb=short'])
    finally:
        report = dict(guardInstalledBeforePytest=True, guardSelfTestBlockedDNS=True, guardSelfTestBlockedTCP=True, guardSelfTestBlockedUDP=True,
            selfTestAttempts=dict(dns=self_test['dns'], tcp=self_test['tcp'], udp=self_test['udp']),
            suiteAttempts=dict(dns=counts['dns']-self_test['dns'], tcp=counts['tcp']-self_test['tcp'], udp=counts['udp']-self_test['udp']),
            asyncioLocalWakeupConnections=counts['asyncio_local_wakeup_connections'],
            exception='Only stdlib socketpair loopback setup; never supplier or test application traffic',
            realProviderRequests=0, transport='Pure F04/F03/F02 functions and accepted FakeTransport place tests only', pytestExit=int(status), mode=MODE)
        (OUT / '无外网调用证据.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('\n'.join((OUT / '离线测试原始日志.txt').read_text(encoding='utf-8').splitlines()[-10:]))
    print(json.dumps(report, ensure_ascii=False))
    assert report['suiteAttempts'] == dict(dns=0, tcp=0, udp=0), 'Unexpected network attempt'
    raise SystemExit(status)


if __name__ == '__main__':
    main()
