"""A fixed localhost provider for synthetic tests; never an Instagram adapter."""

import asyncio
import os
from pathlib import Path
import secrets
import time

from aiohttp import web

from .signin_broker import Broker


USER = 'oak-test-user'
PASSWORD = 'oak-test-password'
OTP = '123456'


class FixtureProvider:
    def __init__(self):
        self.sessions = {}
        self.app = web.Application(client_max_size=2048)
        self.app.add_routes([web.get('/login', self.login), web.post('/login', self.login),
                             web.post('/verify', self.verify), web.get('/account', self.account)])

    def html(self, content):
        return web.Response(text='''<!doctype html><html lang="uk"><meta charset="utf-8">
            <meta name="viewport" content="width=device-width"><title>Oak test provider</title>
            <style>body{font:16px system-ui;background:#fafafa;color:#222;margin:24px}
            h1{font-size:23px}label{display:block;margin-top:16px}input,button{font:inherit;
            padding:12px;box-sizing:border-box;width:100%;margin-top:8px}button{background:#162b21;color:white;border:0;border-radius:8px}p{font-size:13px}</style>
            <h1>Oak test provider</h1><p>Лише синтетичні дані. Це не Instagram.</p>''' + content + '</html>',
            content_type='text/html', headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    async def login(self, request):
        if request.method == 'POST':
            value = await request.post()
            if set(value) == {'identifier','password'} and value['identifier'] == USER and value['password'] == PASSWORD:
                identifier = secrets.token_urlsafe(32)
                self.sessions[identifier] = (time.time() + 300, 'challenge')
                response = self.html('''<form method="post" action="/verify"><label>Тестовий OTP
                    <input name="otp" inputmode="numeric" autocomplete="off" required></label>
                    <button>Підтвердити тест</button></form><p>Тестовий код: 123456.</p>''')
                response.set_cookie('oak_fixture', identifier, httponly=True, samesite='Strict')
                return response
            return self.html('<p>Тестові дані не прийнято. Відкрий новий запит.</p>')
        return self.html('''<form method="post" action="/login"><label>Тестовий ідентифікатор
            <input name="identifier" autocomplete="off" required></label><label>Тестовий пароль
            <input name="password" type="password" autocomplete="off" required></label>
            <button>Увійти в тест</button></form>
            <p>Ідентифікатор: oak-test-user. Пароль: oak-test-password.</p>''')

    async def verify(self, request):
        identifier = request.cookies.get('oak_fixture', '')
        session = self.sessions.get(identifier)
        value = await request.post()
        if session and session[0] > time.time() and session[1] == 'challenge' and set(value) == {'otp'} and value['otp'] == OTP:
            self.sessions[identifier] = (session[0], 'authenticated')
            raise web.HTTPSeeOther('/account')
        return self.html('<p>Тестова перевірка не пройшла.</p>')

    async def account(self, request):
        session = self.sessions.get(request.cookies.get('oak_fixture', ''))
        if not session or session[0] <= time.time() or session[1] != 'authenticated':
            raise web.HTTPForbidden()
        return self.html('<p data-oak-synthetic-authenticated>Синтетичний вхід підтверджено сервером.</p>')


async def serve(socket, parent_port, human_port, provider_port):
    path = Path(socket)
    if (not path.is_absolute() or not path.parent.is_dir() or path.exists()
            or path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o077):
        raise ValueError('Use a new socket in a private temporary directory.')
    provider = FixtureProvider()
    broker = Broker(f'http://127.0.0.1:{human_port}', f'http://127.0.0.1:{parent_port}',
                    f'http://127.0.0.1:{provider_port}')
    runners = []
    try:
        for app in (provider.app, broker.human, broker.control):
            runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
            await runner.setup()
            runners.append(runner)
        await web.TCPSite(runners[0], '127.0.0.1', provider_port).start()
        await web.TCPSite(runners[1], '127.0.0.1', human_port).start()
        await web.UnixSite(runners[2], str(path)).start()
        path.chmod(0o600)
        broker.sweeper = asyncio.create_task(broker.sweep())
        loop = asyncio.get_running_loop()
        import signal
        stopped = asyncio.Event()
        for value in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(value, stopped.set)
        await stopped.wait()
    finally:
        confirmed = await broker.close()
        for runner in reversed(runners):
            await runner.cleanup()
        path.unlink(missing_ok=True)
        if not confirmed:
            raise RuntimeError('Synthetic broker shutdown unconfirmed.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--parent-port', type=int, required=True)
    parser.add_argument('--human-port', type=int, required=True)
    parser.add_argument('--provider-port', type=int, required=True)
    args = parser.parse_args()
    ports = [args.parent_port, args.human_port, args.provider_port]
    if len(set(ports)) != 3 or not all(1024 <= p <= 65535 for p in ports):
        parser.error('Use three distinct unprivileged ports.')
    asyncio.run(serve(args.socket, *ports))
