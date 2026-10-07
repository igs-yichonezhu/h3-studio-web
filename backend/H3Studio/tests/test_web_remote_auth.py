import tempfile
import unittest
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from web_server import StudioWebServer

ORIGIN = 'https://igs-yichonezhu.github.io'
KEY_A = 'h3g_DEMO_ONLY_ALICE_NOT_REAL'
KEY_B = 'h3g_DEMO_ONLY_BOB_NOT_REAL'
MEDIA = '/api/jobs/' + 'a' * 32 + '/video'


class FakeRemotePool:
    def __init__(self, url):
        self.url, self.identities = url, {}
    async def ensure(self, identity, token):
        self.identities[identity] = True
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
