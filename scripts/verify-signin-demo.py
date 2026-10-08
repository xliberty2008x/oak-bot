#!/usr/bin/env python3
"""Exercise only the loopback synthetic Mini App/broker; never real accounts."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.async_api import async_playwright, expect
from oak.signin_fixture import USER, PASSWORD, OTP


async def verify(port):
    base = f'http://127.0.0.1:{port}'
    output = Path('output/playwright')
    output.mkdir(parents=True,exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width':440,'height':960})

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
            await page.screenshot(path=str(output/'broker-completed.png'),full_page=True)
            print('Synthetic login+OTP: original native response sent; secret view removed.',flush=True)

            cancelled,_ = await open_request()
            await page.locator('#input-request-cancel').click()
            await expect(page.locator('#input-request-status')).to_contain_text('Запит скасовано',timeout=15000)
            await expect(page.locator('#input-request-status')).to_contain_text('Надіслано',timeout=15000)
            print('Synthetic owner cancel: original native response sent.',flush=True)

            result = await (await page.request.get(base+'/demo/result')).json()
            rows = {row['id']:row for row in result['requests']}
            assert rows[identifier]['outcome'] == 'authenticated'
            assert rows[cancelled]['outcome'] == 'cancelled'
            assert result['new_turn_starts'] == 0
            assert all(row['cleanup'] == 'confirmed' for row in result['broker'])
            (output/'broker-browser-results.json').write_text(json.dumps(result,indent=2))
            print('No new turn/start; broker cleanup confirmed.',flush=True)
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18786)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use an unprivileged loopback port.')
    asyncio.run(verify(args.port))
