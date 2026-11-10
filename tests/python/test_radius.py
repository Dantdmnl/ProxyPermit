import io
import select
import socket
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from pyrad import packet
from pyrad.dictionary import Dictionary

from app.web import manager


class RadiusLoginTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(select, 'poll'), 'The pyrad client requires select.poll.')
    def test_pap_accept_and_reject_over_udp(self):
        username = 'radius-admin'
        password = 'loopback test passphrase'
        secret = b'loopback-test-shared-secret'
        dictionary = Dictionary(io.StringIO(
            'ATTRIBUTE User-Name 1 string\n'
            'ATTRIBUTE User-Password 2 octets\n'
        ))

        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0))
            server.settimeout(3)

            def reply_once(sign_reply):
                data, address = server.recvfrom(4096)
                request = packet.AuthPacket(packet=data, dict=dictionary, secret=secret)
                self.assertEqual(request.code, packet.AccessRequest)
                self.assertTrue(request.message_authenticator)
                self.assertTrue(request.verify_message_authenticator())
                accepted = request['User-Name'][0] == username and request.PwDecrypt(request['User-Password'][0]) == password
                reply = request.CreateReply(message_authenticator=sign_reply)
                reply.code = packet.AccessAccept if accepted else packet.AccessReject
                server.sendto(reply.ReplyPacket(), address)

            with patch.multiple(
                manager, ENABLE_AUTH=True, ENABLE_RADIUS=True, AUTH_ACCOUNTS_FILE='', AUTH_USERNAME=username,
                AUTH_LOGIN_RATE_LIMIT_ENABLED=False, RADIUS_ALLOW_LOCAL_FALLBACK=False, RADIUS_SERVER='127.0.0.1',
                RADIUS_PORT=server.getsockname()[1], RADIUS_SECRET=secret.decode(), RADIUS_TIMEOUT=1,
                SESSION_STATE_FILE=str(Path(directory) / 'session.json'),
            ), ThreadPoolExecutor(max_workers=1) as pool:
                for supplied, expected, sign_reply in ((password, 200, True), (password, 200, False),
                                                       ('incorrect password', 401, True)):
                    with self.subTest(status=expected, sign_reply=sign_reply):
                        exchange = pool.submit(reply_once, sign_reply)
                        client = manager.app.test_client()
                        response = client.post('/api/auth/login', json={'username': username, 'password': supplied})
                        exchange.result()
                        self.assertEqual(response.status_code, expected)
                        identity = client.get('/api/auth/me').get_json()
                        self.assertEqual(identity['authenticated'], expected == 200)
