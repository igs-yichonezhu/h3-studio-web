import tempfile
import unittest
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from unittest.mock import patch
from web_server import StudioWebServer, custom_gateway_url, origin_value

ORIGIN = 'https://igs-yichonezhu.github.io'
KEY_A = 'h3g_DEMO_ONLY_ALICE_NOT_REAL'
KEY_B = 'h3g_DEMO_ONLY_BOB_NOT_REAL'
MEDIA = '/api/jobs/' + 'a' * 32 + '/video'


class FakeRemotePool:
    def __init__(self, url):
        self.url, self.identities = url, {}
    async def ensure(self, identity, token, *, gateway_url=None):
        self.identities[identity] = gateway_url or True
        return self.url
    def get(self, identity):
        return self.url if identity in self.identities else None
    async def close(self):
        pass


class RemoteAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.keys = {KEY_A, KEY_B}
        self.mode = 'protected'
        self.cookies = []
        async def queue(request):
            self.cookies.append(bool(request.cookies))
            if self.mode == 'offline':
                return web.Response(status=503)
            if self.mode == 'redirect':
                return web.Response(status=302, headers={'Location': '/elsewhere'})
            if self.mode != 'open' and request.headers.get('Authorization', '')[7:] not in self.keys:
                return web.Response(status=401)
            result = web.json_response({'unrelated': True} if self.mode == 'broken' else {'available': False, 'running': [], 'pending': []})
            result.set_cookie('ambient', 'must-not-be-forwarded')
            if self.mode == 'compressed':
                result.enable_compression()
            return result
        gateway = web.Application()
        gateway.router.add_get('/queue', queue)
        self.gateway = TestServer(gateway)
        await self.gateway.start_server()
        worker = web.Application()
        async def projects(request):
            return web.json_response([{'id': 'a' * 32}])
        async def media(request):
            return web.Response(body=b'media')
        worker.router.add_get('/api/editor/projects', projects)
        worker.router.add_get(MEDIA, media)
        self.worker = TestServer(worker)
        await self.worker.start_server()
        self.pool = FakeRemotePool(str(self.worker.make_url('/')).rstrip('/'))
        self.broker = StudioWebServer(Path(self.temp.name), Path(self.temp.name) / 'users',
                                     str(self.gateway.make_url('/')).rstrip('/'), [ORIGIN],
                                     pool=self.pool, remote_auth=True)
        self.client = TestClient(TestServer(self.broker.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.worker.close()
        await self.gateway.close()
        self.temp.cleanup()

    async def login(self, key):
        return await self.client.post('/web/login', headers={'Origin': ORIGIN}, json={'key': key})

    async def test_remote_keys_isolated_and_rechecked_for_session_and_media(self):
        a = await (await self.login(KEY_A)).json()
        b = await (await self.login(KEY_B)).json()
        again = await (await self.login(KEY_A)).json()
        self.assertEqual(a['user']['id'], again['user']['id'])
        self.assertNotEqual(a['user']['id'], b['user']['id'])
        self.assertNotIn(KEY_A, str(a))
        auth = {'Origin': ORIGIN, 'Authorization': 'Bearer ' + a['token']}
        projects = await (await self.client.get('/api/editor/projects', headers=auth)).json()
        ticket = projects['__h3_web_tickets'][MEDIA]
        self.assertEqual((await self.client.get(MEDIA, params={'ticket': ticket})).status, 200)
        self.keys.remove(KEY_A)
        self.assertEqual((await self.client.get('/web/session', headers=auth)).status, 401)
        self.assertEqual((await self.client.get(MEDIA, params={'ticket': ticket})).status, 401)
        self.assertEqual((await self.client.get('/web/session', headers={'Authorization': 'Bearer ' + b['token']})).status, 200)

    async def test_invalid_open_and_redirect_gateways_fail_closed(self):
        self.assertEqual((await self.login('h3g_DEMO_INVALID_NOT_REAL')).status, 401)
        self.assertEqual(len(self.pool.identities), 0)
        for mode in ['open', 'redirect']:
            self.mode = mode
            self.assertEqual((await self.login(KEY_A)).status, 503)
        self.assertEqual(len(self.pool.identities), 0)

    async def test_offline_gpu_keeps_session_but_logout_removes_it(self):
        a = await (await self.login(KEY_A)).json()
        auth = {'Origin': ORIGIN, 'Authorization': 'Bearer ' + a['token']}
        self.mode = 'offline'
        self.assertEqual((await self.client.get('/web/session', headers=auth)).status, 503)
        self.assertEqual(len(self.broker.sessions), 1)
        self.assertEqual((await self.client.post('/web/logout', headers=auth)).status, 200)
        self.assertEqual(len(self.broker.sessions), 0)

    async def test_compressed_json_and_cookie_boundary(self):
        self.mode = 'compressed'
        self.assertEqual((await self.login(KEY_A)).status, 200)
        self.assertEqual((await self.login(KEY_A)).status, 200)
        self.assertFalse(any(self.cookies))
        self.mode = 'broken'
        self.assertEqual((await self.login(KEY_B)).status, 503)

    async def test_fixed_gateway_rejects_a_different_target(self):
        response = await self.client.post('/web/login', headers={'Origin': ORIGIN},
                                          json={'key': KEY_A, 'gateway_url': 'http://192.168.1.2:8190'})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.pool.identities, {})

    async def test_custom_gateways_bind_authentication_and_workspace_to_target(self):
        self.broker.allow_custom_gateways = True
        second_keys = {KEY_A}
        async def queue(request):
            if request.headers.get('Authorization', '')[7:] not in second_keys:
                return web.Response(status=401)
            return web.json_response({'running': [], 'pending': []})
        gateway = web.Application()
        gateway.router.add_get('/queue', queue)
        second = TestServer(gateway)
        await second.start_server()
        self.addAsyncCleanup(second.close)
        first_url = self.broker.gateway_url
        second_url = str(second.make_url('/')).rstrip('/')
        async def selected(key, target):
            return await self.client.post('/web/login', headers={'Origin': ORIGIN},
                                          json={'key': key, 'gateway_url': target})
        # Loopback fake servers are test fixtures; production IP rules are tested below.
        with patch('web_server.custom_gateway_url', side_effect=origin_value):
            a = await (await selected(KEY_A, first_url)).json()
            b = await (await selected(KEY_A, second_url)).json()
            c = await (await selected(KEY_B, first_url)).json()
            again = await (await selected(KEY_A, second_url + '/')).json()
            self.assertEqual(b['user']['id'], again['user']['id'])
            self.assertEqual(len({x['user']['id'] for x in (a, b, c)}), 3)
            self.assertEqual((await selected(KEY_B, second_url)).status, 401)
        self.assertEqual(self.pool.identities[b['user']['id']], second_url)
        auth_a = {'Authorization': 'Bearer ' + a['token']}
        auth_b = {'Authorization': 'Bearer ' + b['token']}
        ticket_a = self.broker.ticket(next(x for x in self.broker.sessions.values() if x['user_id'] == a['user']['id']), MEDIA)
        ticket_b = self.broker.ticket(next(x for x in self.broker.sessions.values() if x['user_id'] == b['user']['id']), MEDIA)
        self.keys.remove(KEY_A)
        self.assertEqual((await self.client.get('/web/session', headers=auth_a)).status, 401)
        self.assertEqual((await self.client.get(MEDIA, params={'ticket': ticket_a})).status, 401)
        self.assertEqual((await self.client.get('/web/session', headers=auth_b)).status, 200)
        self.assertEqual((await self.client.get(MEDIA, params={'ticket': ticket_b})).status, 200)
        self.mode = 'offline'
        self.assertEqual((await self.client.get('/web/session', headers=auth_a)).status, 503)
        self.assertEqual((await self.client.get('/api/editor/projects', headers=auth_b)).status, 200)
        self.assertEqual((await (await self.client.get('/web/session', headers=auth_b)).json())['gateway_url'], second_url)

    async def test_disallowed_targets_are_rejected_before_network_or_worker_start(self):
        self.broker.allow_custom_gateways = True
        for target in ['http://127.0.0.1:8190', 'http://169.254.169.254', 'https://example.com',
                       'http://8.8.8.8', 'http://192.168.1.2/private', 'http://192.168.1.2:0']:
            with self.subTest(target=target):
                response = await self.client.post('/web/login', headers={'Origin': ORIGIN},
                                                  json={'key': KEY_A, 'gateway_url': target})
                self.assertEqual(response.status, 400)
        self.assertEqual(self.pool.identities, {})
        self.assertEqual(self.cookies, [])


class GatewayURLTests(unittest.TestCase):
    def test_company_ip_normalization_and_rejected_special_targets(self):
        self.assertEqual(custom_gateway_url('http://192.168.1.2:8190/'), 'http://192.168.1.2:8190')
        self.assertEqual(custom_gateway_url('https://10.1.2.3:443'), 'https://10.1.2.3')
        self.assertEqual(custom_gateway_url('http://[fd00::1]:8190'), 'http://[fd00::1]:8190')
        for value in ['http://127.0.0.1', 'http://0.0.0.0', 'http://169.254.169.254',
                      'http://100.64.0.1', 'http://192.0.0.1', 'http://224.0.0.1',
                      'http://localhost', 'http://2130706433', 'http://192.168.001.1',
                      'http://[::1]', 'http://[::ffff:192.168.1.2]', 'http://[fd00::1%25eth0]',
                      'http://user:key@192.168.1.2', 'http://192.168.1.2?q=1',
                      'http://192.168.1.2\n', 'http://192.168.1.2:99999', 'http://192.168.1.2:0']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                custom_gateway_url(value)

    def test_custom_gateways_require_remote_authentication(self):
        with tempfile.TemporaryDirectory() as root, self.assertRaises(ValueError):
            StudioWebServer(Path(root), Path(root), 'http://127.0.0.1:8190', [ORIGIN], allow_custom_gateways=True)
