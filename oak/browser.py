"""Observed browser operations, optionally sharing Oak's managed desktop context."""

import asyncio
from contextlib import suppress
import hashlib
import mimetypes
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit, urlunsplit
import uuid


MAX_UPLOAD = 128 * 1024 * 1024
MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024
CONTROL_SELECTOR = 'a,button,input,textarea,select,[role="button"],[role="checkbox"],[contenteditable="true"]'


class BrowserFailure(Exception):
    def __init__(self, code, message, recovery):
        self.code, self.message, self.recovery = code, message, recovery
        super().__init__(message)


class UploadTooLarge(ValueError):
    """Curated local preflight evidence, safe to return through the worker."""

    def __init__(self, observed_bytes):
        self.observed_bytes = observed_bytes
        self.max_bytes = MAX_UPLOAD
        super().__init__('Upload payload exceeds the local browser upload limit.')


def _safe_url(value):
    """Navigation state must not echo authorization query strings or fragments."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'}:
            return parsed.scheme + ':' if parsed.scheme else ''
        host = parsed.hostname or ''
        if ':' in host:
            host = '[' + host + ']'
        if parsed.port:
            host += ':' + str(parsed.port)
        return urlunsplit((parsed.scheme, host, parsed.path[:2000], '', ''))
    except ValueError:
        return ''


# No input value, hidden field, form payload or page script is included. File
# inputs remain discoverable even when custom upload widgets hide the input.
_OBSERVE = r'''({selector, limit}) => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const label = e => (e.getAttribute('aria-label') ||
    (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => document.getElementById(id)?.innerText || '').join(' ').trim() ||
    Array.from(e.labels || []).map(n => n.innerText).join(' ') || e.innerText ||
    e.getAttribute('placeholder') || e.getAttribute('title') || '').trim().slice(0, 160);
  const controls = [];
  const elements = document.querySelectorAll(selector);
  for (let i = 0; i < elements.length && controls.length < limit; i++) {
    const e = elements[i], type = (e.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden' || (!visible(e) && type !== 'file')) continue;
    const item = {selector: ':is(' + selector + ') >> nth=' + i,
      tag: e.tagName.toLowerCase(), text: label(e), type,
      visible: visible(e), disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true'};
    if (e.getAttribute('placeholder')) item.placeholder = e.getAttribute('placeholder').slice(0, 160);
    if (type === 'file') {
      item.accept = (e.accept || '').slice(0, 250); item.multiple = !!e.multiple;
      item.hint = 'Use upload with this selector; a hidden file input does not need a desktop file dialog.';
    }
    if (e.tagName === 'A') item.href = e.href;
    if (e.tagName === 'SELECT') item.options = Array.from(e.options).slice(0, 40).map(o => ({label: o.label.slice(0, 160), value: o.value.slice(0, 160)}));
    controls.push(item);
  }
  return {text: (document.body?.innerText || '').slice(0, 20000), controls};
}'''


class BrowserTools:
    def __init__(self, workspace, context_provider=None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.profile = self.workspace / 'browser-profile'
        self.artifacts = self.workspace / 'artifacts'
        self._context_provider = context_provider
        self._playwright = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()
        self._tab_ids = {}
        self._frame_ids = {}
        self._next_tab = 0
        self._next_frame = 0
        self._redactions = []

    async def _start(self):
        if self._context_provider is not None:
            context = await self._context_provider()
            if context is None:
                raise BrowserFailure('browser_unavailable', 'The managed browser is not available.',
                                     'Check the managed desktop browser state.')
            if context is not self._context:
                self._context = context
                self._page = None
                self._tab_ids.clear()
                self._frame_ids.clear()
        if self._context is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserFailure('browser_unavailable', 'Playwright is not installed.',
                                 'Install Playwright and its Chromium browser.') from None
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile), headless=True, viewport={'width': 1280, 'height': 800},
                accept_downloads=True, chromium_sandbox=True)
            self._context.set_default_timeout(15000)
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            raise BrowserFailure('browser_unavailable', 'Chromium could not start.',
                                 'Check Chromium installation and the browser environment.') from None

    def _tab_id(self, page):
        if page not in self._tab_ids:
            self._next_tab += 1
            self._tab_ids[page] = 'tab-' + str(self._next_tab)
        return self._tab_ids[page]

    def _frame_id(self, frame):
        if frame not in self._frame_ids:
            self._next_frame += 1
            self._frame_ids[frame] = 'frame-' + str(self._next_frame)
        return self._frame_ids[frame]

    def _redact(self, value):
        if isinstance(value, str):
            for secret in self._redactions:
                value = value.replace(secret, '[entered text]')
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            # Opaque IDs and generated selectors must remain actionable even if
            # a short entered value happens to occur in their spelling.
            structural = {'id', 'parent', 'frame', 'tab', 'selector', 'tag', 'type', 'hint'}
            return {key: item if key in structural else self._redact(item) for key, item in value.items()}
        return value

    async def _page_for(self, tab):
        pages = [page for page in self._context.pages if not page.is_closed()]
        self._tab_ids = {page: identifier for page, identifier in self._tab_ids.items() if page in pages}
        for page in pages:
            self._tab_id(page)
        if tab is not None:
            page = next((page for page in pages if self._tab_id(page) == tab), None)
            if page is None:
                raise BrowserFailure('tab_not_found', 'The selected tab is no longer available.',
                                     'Read tabs and use a current tab ID.')
            self._page = page
        elif self._context_provider is not None:
            # Visual computer actions and programmatic operations use the same
            # active tab. The provider owns this context and its account state.
            candidates = ([self._page] if self._page in pages else []) + [page for page in pages if page is not self._page]
            for page in candidates:
                with suppress(Exception):
                    if await asyncio.wait_for(page.evaluate('document.visibilityState === "visible"'), 1):
                        self._page = page
                        break
        if self._page not in pages:
            self._page = pages[-1] if pages else await self._context.new_page()
        self._tab_id(self._page)
        return self._page

    def _frame_for(self, page, identifier):
        for frame in page.frames:
            self._frame_id(frame)
        if identifier is None or identifier == 'main':
            return page.main_frame
        found = next((frame for frame in page.frames if self._frame_id(frame) == identifier), None)
        if found is None:
            raise BrowserFailure('frame_not_found', 'The selected frame is no longer available.',
                                 'Read the page and use a current frame ID from frames or controls.')
        return found

    async def _observe(self, page):
        if page is None or page.is_closed():
            return {'url': '', 'title': '', 'text': '', 'controls': [], 'frames': [], 'tabs': []}
        value = {'url': _safe_url(page.url), 'title': '', 'text': '', 'controls': [], 'frames': [], 'tabs': []}
        with suppress(Exception):
            value['title'] = (await asyncio.wait_for(page.title(), 1))[:300]
        budget = 20000
        deadline = time.monotonic() + 4
        current_frames = set(page.frames)
        self._frame_ids = {frame: identifier for frame, identifier in self._frame_ids.items()
                           if not frame.is_detached()}
        for frame in list(page.frames)[:32]:
            info = {'id': self._frame_id(frame), 'url': _safe_url(frame.url),
                    'name': frame.name[:160], 'main': frame is page.main_frame}
            if frame.parent_frame in current_frames:
                info['parent'] = self._frame_id(frame.parent_frame)
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                observation = await asyncio.wait_for(frame.evaluate(
                    _OBSERVE, {'selector': CONTROL_SELECTOR, 'limit': max(0, 100 - len(value['controls']))}), min(1, remaining))
                info['text'] = observation['text'][:budget]
                budget -= len(info['text'])
                if info['text']:
                    value['text'] += ('\n' if value['text'] else '') + info['text']
                controls = [{**control, 'frame': info['id']} for control in observation['controls']]
                for control in controls:
                    if 'href' in control:
                        control['href'] = _safe_url(control['href'])
                value['controls'].extend(controls)
            except Exception:
                info['unavailable'] = True
            value['frames'].append(info)
        for other in self._context.pages[:32]:
            if not other.is_closed():
                value['tabs'].append({'id': self._tab_id(other), 'url': _safe_url(other.url), 'active': other is page})
        value['tab'] = self._tab_id(page)
        return self._redact(value)

    @staticmethod
    def _validate(action, url, selector, text, frame, files, timeout_ms, tab):
        if action not in {'open', 'read', 'click', 'type', 'screenshot', 'upload', 'download', 'wait', 'tabs', 'switch'}:
            raise ValueError('Unknown browser action. Use open/read/click/type/screenshot/upload/download/wait/tabs/switch.')
        if action == 'open':
            parsed = urlsplit(url or '')
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Open requires an HTTP or HTTPS URL without embedded credentials.')
        if action in {'click', 'type', 'upload', 'download'} or selector is not None:
            if not isinstance(selector, str) or not selector.strip() or len(selector) > 1000:
                raise ValueError('This browser action requires a CSS or Playwright selector.')
        if action == 'type' and (not isinstance(text, str) or len(text) > 20000):
            raise ValueError('Typed text must contain at most 20000 characters.')
        if frame is not None and (not isinstance(frame, str) or len(frame) > 80):
            raise ValueError('Use a frame ID from the latest browser observation.')
        if tab is not None and (not isinstance(tab, str) or len(tab) > 80):
            raise ValueError('Use a tab ID from the latest browser observation.')
        if action == 'switch' and tab is None:
            raise ValueError('Switch requires a tab ID.')
        if timeout_ms is not None and (type(timeout_ms) is not int or not 100 <= timeout_ms <= 60000):
            raise ValueError('timeout_ms must be an integer between 100 and 60000.')
        if action == 'upload' and (not isinstance(files, list) or not 1 <= len(files) <= 8 or
                                  any(not isinstance(path, str) or not path for path in files)):
            raise ValueError('Upload requires one to eight workspace file paths.')

    async def _locator(self, frame, selector, timeout):
        locator = frame.locator(selector)
        if await locator.count() == 0:
            try:
                await locator.first.wait_for(state='attached', timeout=timeout)
            except Exception:
                raise BrowserFailure('selector_not_found', 'No matching control appeared.',
                                     'Inspect the fresh controls and frames, then choose an observed selector.') from None
        if await locator.count() != 1:
            raise BrowserFailure('ambiguous_selector', 'The selector matches more than one control.',
                                 'Choose one control using its observed selector and frame.')
        return locator

    def _upload_payloads(self, files):
        """Snapshot held regular file descriptors; never pass mutable paths to Chromium."""
        result, total = [], 0
        for value in files:
            path = Path(value)
            if path.is_absolute():
                try:
                    path = path.relative_to(self.workspace)
                except ValueError:
                    raise ValueError('Upload files must be inside the Oak workspace.') from None
            parts = path.parts
            if not parts or any(part.startswith('.') or part.lower() in {'secrets', 'credentials', 'browser-profile'}
                                for part in parts):
                raise ValueError('Private service files cannot be uploaded.')
            directory = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            descriptor = None
            try:
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                    os.close(directory)
                    directory = child
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise ValueError('Upload requires regular workspace files without hardlinks.')
                if before.st_size > MAX_UPLOAD - total:
                    raise UploadTooLarge(total + before.st_size)
                chunks, size = [], 0
                while True:
                    chunk = os.read(descriptor, min(1024 * 1024, MAX_UPLOAD - total - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD - total:
                        raise UploadTooLarge(total + size)
                    chunks.append(chunk)
                after = os.fstat(descriptor)
                identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
                if identity(before) != identity(after) or size != before.st_size:
                    raise ValueError('Upload source changed while reading; create a stable copy and retry.')
                result.append({'name': parts[-1], 'mimeType': mimetypes.guess_type(parts[-1])[0] or 'application/octet-stream',
                               'buffer': b''.join(chunks)})
                total += size
            except OSError:
                raise ValueError('Upload source is missing or unsafe; use a regular workspace file without symlinks.') from None
            finally:
                os.close(directory)
                if descriptor is not None:
                    os.close(descriptor)
        return result

    def _save_artifact(self, source, extension, maximum=MAX_DOWNLOAD):
        """Write a bounded receipt through held directories, never site-supplied paths."""
        root = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        directory, descriptor = None, None
        name = uuid.uuid4().hex + extension
        try:
            with suppress(FileExistsError):
                os.mkdir('artifacts', mode=0o700, dir_fd=root)
            directory = os.open('artifacts', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            digest, size = hashlib.sha256(), 0
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                size += len(chunk)
                if size > maximum:
                    raise ValueError('Downloaded file exceeds the 2 GiB artifact limit.')
                digest.update(chunk)
                pending = memoryview(chunk)
                while pending:
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise OSError('Artifact write did not progress.')
                    pending = pending[written:]
            os.fsync(descriptor)
            return {'path': str(self.artifacts / name), 'size': size, 'sha256': digest.hexdigest()}
        except BaseException:
            if descriptor is not None:
                with suppress(OSError):
                    os.unlink(name, dir_fd=directory)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
            os.close(root)

    async def run(self, action='read', url=None, selector=None, text=None, frame=None,
                  files=None, timeout_ms=None, tab=None):
        self._validate(action, url, selector, text, frame, files, timeout_ms, tab)
        # Before any page side effect, take immutable upload payloads.
        try:
            payloads = await asyncio.to_thread(self._upload_payloads, files) if action == 'upload' else None
        except ValueError as exc:
            # Expected local validation must travel as a result. Raising through
            # the execution worker would retain only the exception class, losing
            # the size/stage needed to recover without handing the task back.
            error = {'code': 'upload_source_invalid', 'stage': 'upload_preflight',
                     'message': 'The upload source could not be safely prepared.',
                     'recovery': 'Inspect the workspace source and use a stable regular file; preserve the original.'}
            if isinstance(exc, UploadTooLarge):
                error.update(code='payload_too_large', message=str(exc),
                             observed_bytes=exc.observed_bytes, max_bytes=exc.max_bytes,
                             recovery='The payload was not sent to the site. Inspect an existing valid prepared copy, '
                                      'or prepare a separate smaller copy within the authorized quality and content choices; '
                                      'preserve the original. If exact original bytes are required, use an already authorized '
                                      'supported upload route with a sufficient limit.')
            else:
                # Only these source-owned messages are safe. Never return an
                # arbitrary exception, path, entered value or transport detail.
                curated = {
                    'Upload files must be inside the Oak workspace.',
                    'Private service files cannot be uploaded.',
                    'Upload requires regular workspace files without hardlinks.',
                    'Upload source changed while reading; create a stable copy and retry.',
                    'Upload source is missing or unsafe; use a regular workspace file without symlinks.',
                }
                if str(exc) in curated:
                    error['message'] = str(exc)
            return {'ok': False, 'action': action, 'outcome': 'not_attempted', 'error': error}
        timeout = timeout_ms or (30000 if action in {'open', 'download'} else 15000)
        if action == 'type' and text:
            self._redactions = [item for item in self._redactions if item != text][-63:] + [text]
        async with self._lock:
            page, attempted, receipt, download = None, False, None, None
            popup_ready, popup_listener = asyncio.Event(), None
            try:
                await self._start()
                page = await self._page_for(tab)
                if tab is not None:
                    await page.bring_to_front()
                target = self._frame_for(page, frame)
                previous_pages = set(self._context.pages)
                if action in {'click', 'upload'}:
                    popup_listener = lambda _page: popup_ready.set()
                    self._context.on('page', popup_listener)
                if action == 'open':
                    attempted = True
                    await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
                elif action in {'click', 'type', 'upload', 'download'}:
                    locator = await self._locator(target, selector, timeout)
                    if action == 'type':
                        element = await locator.element_handle(timeout=timeout)
                        if element is None:
                            raise BrowserFailure('selector_not_found', 'The selected control is no longer available.',
                                                 'Inspect the fresh controls and choose the current input.')
                        attempted = True
                        try:
                            await element.fill(text, timeout=timeout)
                            # Input events can synchronously advance the form or
                            # replace this node. Never resolve the selector again
                            # and confuse a new field with the one just filled.
                            actual = None
                            with suppress(Exception):
                                actual = await asyncio.wait_for(element.evaluate(
                                    'e => e.isContentEditable ? e.innerText : e.value'), .25)
                            receipt = {'dispatched': True,
                                       'filled': actual == text if isinstance(actual, str) else None,
                                       'verification': 'observed_original_control' if isinstance(actual, str) else 'input_dispatch',
                                       'note': 'Input dispatched; the page may reset or replace the field. Inspect the current observation to verify the next step.'}
                        finally:
                            with suppress(Exception):
                                await asyncio.wait_for(element.dispose(), 1)
                    elif action == 'upload':
                        is_file = await locator.evaluate('e => e.tagName === "INPUT" && e.type === "file"')
                        attempted = True
                        if is_file:
                            await locator.set_input_files(payloads, timeout=timeout)
                        else:
                            async with page.expect_file_chooser(timeout=timeout) as chooser_event:
                                await locator.click(timeout=timeout)
                            chooser = await chooser_event.value
                            await chooser.set_files(payloads, timeout=timeout)
                        # Successful native selection dispatch is the receipt.
                        # Reactive uploaders often replace or reset the input
                        # synchronously in onchange; querying the selector again
                        # could read another control or falsely fail a real upload.
                        receipt = {'selected_files': len(payloads),
                                   'bytes': sum(len(item['buffer']) for item in payloads),
                                   'verified': True, 'verification': 'browser_file_selection',
                                   'server_upload_verified': False,
                                   'note': 'Browser file selection completed; inspect the page response to verify server upload.'}
                    elif action == 'download':
                        attempted = True
                        async with page.expect_download(timeout=timeout) as event:
                            await locator.click(timeout=timeout)
                        download = await event.value
                        async with asyncio.timeout(timeout / 1000):
                            local_path = await download.path()
                            failure = await download.failure()
                        if failure or local_path is None:
                            raise BrowserFailure('download_failed', 'The browser did not finish the download.',
                                                 'Inspect the page response and download availability before retrying.')
                        suffix = Path(download.suggested_filename).suffix
                        suffix = suffix if len(suffix) <= 12 and suffix[1:].isalnum() else ''
                        with open(local_path, 'rb') as source:
                            receipt = await asyncio.to_thread(self._save_artifact, source, suffix)
                        receipt.update({'verified': True, 'delivery': 'Use oak_send_file with path to deliver this artifact.'})
                    else:
                        attempted = True
                        await locator.click(timeout=timeout)
                    if action in {'click', 'upload'}:
                        # Chromium may finish the click before delivering a new
                        # page event. Catch it without a long navigation delay.
                        if not popup_ready.is_set():
                            with suppress(TimeoutError):
                                await asyncio.wait_for(popup_ready.wait(), min(timeout / 1000, .25))
                        new_pages = [item for item in self._context.pages if item not in previous_pages and not item.is_closed()]
                        if new_pages:
                            page = self._page = new_pages[-1]
                            await page.bring_to_front()
                            with suppress(Exception):
                                await page.wait_for_load_state('domcontentloaded', timeout=min(timeout, 5000))
                elif action == 'wait':
                    if selector:
                        await target.locator(selector).wait_for(state='visible', timeout=timeout)
                    else:
                        await asyncio.sleep(min(timeout_ms or 500, 5000) / 1000)
                elif action == 'screenshot':
                    import io
                    image = await page.screenshot(full_page=False, timeout=timeout)
                    receipt = await asyncio.to_thread(self._save_artifact, io.BytesIO(image), '.png')
                result = await self._observe(page)
                result.update({'ok': True, 'action': action, 'outcome': 'completed'})
                if receipt is not None:
                    result['receipt'] = receipt
                    if 'path' in receipt:
                        result['path'] = receipt['path']
                if action == 'click':
                    result['verification'] = 'Click dispatched. Inspect this observation to verify the intended website result.'
                return result
            except Exception as exc:
                if isinstance(exc, BrowserFailure):
                    error = {'code': exc.code, 'message': exc.message, 'recovery': exc.recovery}
                else:
                    code = 'action_timeout' if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or type(exc).__name__ == 'TimeoutError' else 'action_failed'
                    error = {'code': code, 'message': 'The browser could not confirm this action completed.',
                             'recovery': 'Inspect the current page, controls and frames; choose another evidenced method if needed.'}
                    # Inspect state rather than printing Playwright's exception,
                    # which may contain entered text, authorization URLs or files.
                    if page is not None and not page.is_closed() and selector:
                        with suppress(Exception):
                            current = self._frame_for(page, frame).locator(selector)
                            if await current.count() == 1:
                                if not await current.is_visible():
                                    error['code'] = 'control_hidden'
                                elif not await current.is_enabled():
                                    error['code'] = 'control_disabled'
                result = await self._observe(page)
                uncertain = attempted and action in {'click', 'type', 'upload', 'download', 'open'}
                if uncertain:
                    error['recovery'] += ' The action may have reached the site; verify before repeating any external change.'
                result.update({'ok': False, 'action': action, 'outcome': 'uncertain' if uncertain else ('not_completed' if attempted else 'not_attempted'), 'error': error})
                return result
            finally:
                if popup_listener is not None and self._context is not None:
                    self._context.remove_listener('page', popup_listener)
                if download is not None:
                    with suppress(Exception):
                        await download.cancel()
                    with suppress(Exception):
                        await download.delete()

    async def close(self):
        async with self._lock:
            try:
                if self._context is not None and self._context_provider is None:
                    await self._context.close()
            finally:
                self._context = self._page = None
                self._tab_ids.clear()
                self._frame_ids.clear()
                self._redactions.clear()
                if self._playwright is not None:
                    await self._playwright.stop()
                    self._playwright = None
