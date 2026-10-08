#!/usr/bin/env python3
"""Exercise only the loopback synthetic Mini App/broker; never real accounts."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright, expect
from oak.signin_fixture import USER, PASSWORD, OTP


@asynccontextmanager
async def managed_browser():
    """Finish resource acquisition before propagating cancellation, then close it."""
    manager = async_playwright()
    pw = browser = None
    startup = asyncio.create_task(manager.start())
    try:
        try:
            pw = await asyncio.shield(startup)
        except asyncio.CancelledError:
            pw = await asyncio.wait_for(asyncio.shield(startup), 15)
            raise
        launch = asyncio.create_task(pw.chromium.launch(headless=True, timeout=15000))
        try:
            browser = await asyncio.shield(launch)
        except asyncio.CancelledError:
            browser = await asyncio.wait_for(asyncio.shield(launch), 20)
            raise
        yield browser
    finally:
        async def close():
            failed = False
            if browser is not None:
                try:
                    await asyncio.wait_for(browser.close(), 10)
                except Exception:
                    failed = True
            if pw is not None:
                try:
                    await asyncio.wait_for(pw.stop(), 10)
                except Exception:
                    failed = True
            else:
                if not startup.done():
                    startup.cancel()
                    failed = True
                try:
                    await asyncio.wait_for(manager.__aexit__(None, None, None), 10)
                except Exception:
                    failed = True
            return not failed
        cleanup = asyncio.create_task(close())
        cancelled = False
        while True:
            try:
                confirmed = await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                if cleanup.cancelled():
                    raise RuntimeError('Synthetic client cleanup unconfirmed.') from None
                cancelled = True
        if not confirmed:
            raise RuntimeError('Synthetic client cleanup unconfirmed.')
        if cancelled:
            raise asyncio.CancelledError


async def verify(port, output=None):
    base = f'http://127.0.0.1:{port}'
    if output is not None:
        output = Path(output)
        output.mkdir(parents=True,exist_ok=True)
    async with managed_browser() as browser:
        page = await browser.new_page(viewport={'width':440,'height':960})

        async def open_form():
            response = await page.request.post(base+'/demo/request/task_details')
            assert response.status == 200
            identifier = (await response.json())['id']
            # A stale surface/conversation must never retarget the authenticated request.
            await page.goto(base+'?conversation=topic%3A999&surface=stale&request='+identifier,
                            wait_until='domcontentloaded',timeout=60000)
            await expect(page.locator('#request-field-details')).to_be_visible()
            assert await page.evaluate('selectedSession') == 'telegram'
            assert await page.evaluate('surfaceLaunch') is None
            assert await page.locator('#input-request-fields input[type=password]').count() == 0
            return identifier

        submitted = await open_form()
        await page.locator('#request-field-details').fill('Синтетична задача bootstrap')
        await page.locator('#input-request-submit').click()
        await expect(page.locator('#input-request-status')).to_contain_text('Відповідь збережено',timeout=15000)
        await expect(page.locator('#input-request-status')).to_contain_text('Надіслано',timeout=15000)
        form_cancelled = await open_form()
        await page.locator('#input-request-cancel').click()
        await expect(page.locator('#input-request-status')).to_contain_text('Запит скасовано',timeout=15000)
        await expect(page.locator('#input-request-status')).to_contain_text('Надіслано',timeout=15000)

        async def open_request():
            response = await page.request.post(base+'/demo/request/synthetic_sign_in')
            assert response.status == 200
            identifier = (await response.json())['id']
            await page.goto(base+'?conversation=telegram&request='+identifier,
                            wait_until='domcontentloaded',timeout=60000)
            await page.get_by_role('button',name='Відкрити тестовий канал').click()
            frame = page.frame_locator('.signin-broker')
            await frame.locator('#screen').wait_for(state='visible',timeout=60000)
            return identifier,frame

        async def pointer(frame,y):
            image = frame.locator('#screen')
            box = await image.bounding_box()
            # Coordinates grounded in the viewed fixed fixture screenshot;
            # no access to the broker context, provider DOM or cookies.
            await image.click(position={'x':box['width']/2,'y':y*box['height']/530})

        async def type_fixture(frame,value):
            text = frame.locator('#text')
            await expect(text).to_be_enabled()
            await text.fill(value)
            await frame.get_by_role('button',name='Ввести',exact=True).click()
            await expect(text).to_have_value('')

        identifier,frame = await open_request()
        if output is not None:
            await page.screenshot(path=str(output/'broker-empty.png'),full_page=True)
        initial = await frame.locator('#screen').get_attribute('data-navigation-epoch')
        await pointer(frame,146)
        await type_fixture(frame,USER)
        await pointer(frame,234)
        await type_fixture(frame,PASSWORD)
        await pointer(frame,286)
        await expect(frame.locator('#screen')).not_to_have_attribute('data-navigation-epoch',initial)
        await pointer(frame,146)
        await type_fixture(frame,OTP)
        await pointer(frame,201)
        await expect(page.locator('#input-request-status')).to_contain_text('Синтетичний вхід перевірено',timeout=15000)
        await expect(page.locator('#input-request-status')).to_contain_text('Надіслано',timeout=15000)
        assert await page.locator('.signin-broker').count() == 0
        if output is not None:
            await page.screenshot(path=str(output/'broker-completed.png'),full_page=True)

        cancelled,_ = await open_request()
        await page.locator('#input-request-cancel').click()
        await expect(page.locator('#input-request-status')).to_contain_text('Запит скасовано',timeout=15000)
        await expect(page.locator('#input-request-status')).to_contain_text('Надіслано',timeout=15000)

        result = await (await page.request.get(base+'/demo/result')).json()
        rows = {row['id']:row for row in result['requests']}
        assert rows[submitted]['outcome'] == 'submitted'
        assert rows[form_cancelled]['outcome'] == 'cancelled'
        assert rows[identifier]['outcome'] == 'authenticated'
        assert rows[cancelled]['outcome'] == 'cancelled'
        assert all(rows[key]['delivery'] == 'sent' for key in (submitted,form_cancelled,identifier,cancelled))
        assert result['native_attempts'] == 4
        assert result['new_turn_starts'] == 0
        assert all(row['cleanup'] == 'confirmed' for row in result['broker'])
        if output is not None:
            (output/'broker-browser-results.json').write_text(json.dumps(result,indent=2))
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18786)
    parser.add_argument('--output',default='output/playwright')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged loopback port.')
    asyncio.run(verify(args.port,args.output))
    print('Synthetic forms/login/OTP/cancel verified; four native responses, no new turns, cleanup confirmed.')
