"""Oak's subscription runtime transport and interactive tool dispatch."""

import asyncio
import contextlib
import json
import os
from pathlib import Path

MODEL = "gpt-6.1-sol"


def _toml_value(value):
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(key)} = {_toml_value(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if value is None:
        raise ValueError("Runtime config values cannot be null.")
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class RpcError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def require_chatgpt_account(result):
    account = result.get("account") or {}
    if account.get("type") != "chatgpt":
        raise RpcError(-32000, "ChatGPT login required; API-key billing is disabled. Run codex login.")


class RuntimeClient:
    def __init__(self, cwd=None, sandbox="workspace-write", approval_handler=None,
                 tool_handler=None, request_input_handler=None, config=None, home=None):
        if sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("sandbox must be read-only or workspace-write")
        self.cwd = cwd
        self.sandbox = sandbox
        self.config = dict(config or {})
        self.approval_policy = self.config.get("approval_policy", "on-request")
        if self.approval_policy not in {"on-request", "untrusted", "never"}:
            raise ValueError("Unsupported approval_policy")
        self.approval_handler = approval_handler
        self.tool_handler = tool_handler
        self.request_input_handler = request_input_handler
        self.events = asyncio.Queue()
        self.process = None
        self._pending = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._reader = None
        self._stderr = None
        self._authenticated = False
        self._closing = False
        self._server_requests = {}
        self.home = Path(home if home is not None else os.environ.get(
            'CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def start(self):
        if self.process is not None:
            return self
        self._closing = False
        env = os.environ.copy()
        env['CODEX_HOME'] = str(self.home)
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            env.pop(name, None)
        config = {"model_reasoning_effort": "low", **self.config,
                  "model_provider": "openai", "model": MODEL,
                  "forced_login_method": "chatgpt", "approval_policy": self.approval_policy,
                  "sandbox_mode": self.sandbox}
        overrides = []
        for key, value in config.items():
            overrides.extend(("-c", f"{key}={_toml_value(value)}"))
        self.process = await asyncio.create_subprocess_exec(
            "codex", "app-server", "--listen", "stdio://", *overrides,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=self.cwd, env=env,
            limit=16 * 1024 * 1024,
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr = asyncio.create_task(self._drain_stderr())
        try:
            initialized = await self.request("initialize", {"clientInfo": {
                "name": "oak", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            if initialized.get('codexHome'):
                self.home = Path(initialized['codexHome']).expanduser().resolve()
            await self._send({"method": "initialized"})
            require_chatgpt_account(await self.request("account/read", {"refreshToken": False}))
            self._authenticated = True
        except BaseException:
            await self.close()
            raise
        return self

    async def request(self, method, params=None):
        params = dict(params or {})
        if method in {"thread/start", "thread/resume", "thread/fork", "turn/start", "turn/steer"}:
            if not self._authenticated:
                raise RpcError(-32000, "ChatGPT authentication is required before execution.")
        if method in {"thread/start", "thread/resume", "thread/fork"}:
            params.update(model=MODEL, modelProvider="openai", sandbox=self.sandbox,
                          approvalPolicy=self.approval_policy, approvalsReviewer="user")
        if method == "turn/start":
            policy = {"type": "readOnly" if self.sandbox == "read-only" else "workspaceWrite",
                      "networkAccess": False}
            params.update(model=MODEL, approvalPolicy=self.approval_policy,
                          approvalsReviewer="user", sandboxPolicy=policy)
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            result = await asyncio.wait_for(future, timeout=60)
            if method in {"thread/start", "thread/resume", "thread/fork"}:
                if result.get("model") != MODEL or result.get("modelProvider") != "openai":
                    raise RpcError(-32000, "Runtime returned an unexpected model or provider; execution stopped.")
            return result
        finally:
            self._pending.pop(request_id, None)

    async def _send(self, message):
        async with self._write_lock:
            if self.process is None or self.process.returncode is not None:
                raise ConnectionError("Agent runtime is not running.")
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            await self.process.stdin.drain()

    async def _read_loop(self):
        failure = "Agent runtime disconnected."
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message:
                    if "id" in message:
                        task = asyncio.create_task(self._handle_server_request(message))
                        request_id = message["id"]
                        self._server_requests[request_id] = task
                        task.add_done_callback(lambda done, key=request_id: self._server_request_done(key, done))
                    else:
                        if message["method"] == "account/updated":
                            self._authenticated = message.get("params", {}).get("authMode") == "chatgpt"
                        elif message["method"] == "serverRequest/resolved":
                            task = self._server_requests.get(message.get("params", {}).get("requestId"))
                            if task is not None:
                                task.cancel()
                        await self.events.put(message)
                elif "id" in message:
                    future = self._pending.get(message["id"])
                    if future is not None and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(RpcError(error.get("code"), error.get("message", "Runtime request failed.")))
                        else:
                            future.set_result(message.get("result", {}))
        except asyncio.CancelledError:
            raise
        except Exception:
            failure = "Agent runtime transport failed."
        finally:
            self._authenticated = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError(failure))
            tasks = tuple(self._server_requests.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not self._closing:
                await self.events.put({"method": "client/disconnected", "params": {"message": failure}})

    def _server_request_done(self, request_id, task):
        if self._server_requests.get(request_id) is task:
            self._server_requests.pop(request_id)
        if not task.cancelled():
            task.exception()  # Retrieve errors without logging callback/account data.

    async def _handle_server_request(self, message):
        """Handlers receive {requestId, method, ...params}; return native result dicts."""
        method = message["method"]
        handler = None
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            response = {"result": {"decision": "decline"}}
            handler = self.approval_handler
        elif method == "item/permissions/requestApproval":
            response = {"result": {"permissions": {}, "scope": "turn"}}
            handler = self.approval_handler
        elif method == "item/tool/requestUserInput":
            response = {"result": {"answers": {}}}
            handler = self.request_input_handler
        elif method == "mcpServer/elicitation/request":
            response = {"result": {"action": "decline"}}
            handler = self.request_input_handler
        elif method == "item/tool/call":
            response = {"result": {"success": False, "contentItems": [
                {"type": "inputText", "text": "This tool is not available in Oak."}]}}
            handler = self.tool_handler
        else:
            response = {"error": {"code": -32601, "message": "This client does not support this server request."}}
        params = message.get("params", {})
        handled = False
        if handler is not None:
            try:
                result = await asyncio.wait_for(handler({**params, "requestId": message["id"], "method": method}), 600)
                if not isinstance(result, dict):
                    raise TypeError("Handler must return a native result dictionary")
                json.dumps(result, allow_nan=False)
                response = {"result": result}
                handled = True
            except Exception:
                # Use the safe default on callback timeout/failure. Never leak its data.
                pass
        await self._send({"id": message["id"], **response})
        if not handled:
            await self.events.put({"method": "client/requestDenied", "params": {
                "threadId": params.get("threadId"), "turnId": params.get("turnId"),
                "requestMethod": method, "message": "No interactive handler completed this request; it was declined.",
            }})

    async def _drain_stderr(self):
        # Never forward subprocess diagnostics: they can contain account details.
        while await self.process.stderr.read(65536):
            pass

    async def close(self):
        self._closing = True
        self._authenticated = False
        tasks = tuple(self._server_requests.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        for task in (self._reader, self._stderr):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self.process = None
