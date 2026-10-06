import unittest
from unittest.mock import AsyncMock, patch

from oak.runtime import RuntimeClient, RpcError, require_chatgpt_account
from oak.events import EventMapper, to_agui_event, validate_event
from oak.controller import Controller


class EventTests(unittest.TestCase):
    def test_stream_and_final_snapshot_do_not_duplicate_text(self):
        mapper = EventMapper()
        events = []
        for message in [
            {"method": "turn/started", "params": {"threadId": "t", "turn": {"id": "r"}}},
            {"method": "item/agentMessage/delta", "params": {"threadId": "t", "turnId": "r", "itemId": "a", "delta": "Hi "}},
            {"method": "item/completed", "params": {"threadId": "t", "turnId": "r", "item": {"type": "agentMessage", "id": "a", "text": "Hi world", "phase": "final_answer"}}},
            {"method": "turn/completed", "params": {"threadId": "t", "turn": {"id": "r", "status": "completed", "items": [{"type": "agentMessage", "id": "a", "text": "Hi world", "phase": "final_answer"}]}}},
        ]:
            events.extend(mapper.feed(message))
        self.assertEqual("".join(e.get("delta", "") for e in events), "Hi world")
        self.assertEqual([e["type"] for e in events], [
            "RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
            "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "RUN_FINISHED"])
        self.assertTrue(all(e["threadId"] == "t" and e["runId"] == "r" for e in events))
        self.assertEqual(events[-2]["phase"], "final_answer")

    def test_reasoning_and_tool_output_never_become_messages(self):
        mapper = EventMapper()
        for method, extra in [
            ("item/reasoning/textDelta", {"delta": "private"}),
            ("item/commandExecution/outputDelta", {"delta": "secret"}),
            ("item/completed", {"item": {"type": "reasoning", "id": "a", "text": "private"}}),
        ]:
            self.assertEqual(mapper.feed({"method": method, "params": {"threadId": "t", "turnId": "r", **extra}}), [])

    def test_failed_attempt_waits_for_terminal_failure(self):
        mapper = EventMapper()
        self.assertEqual(mapper.feed({"method": "error", "params": {
            "threadId": "t", "turnId": "r", "willRetry": True, "error": {"message": "retry"}}}), [])
        events = mapper.feed({"method": "turn/completed", "params": {
            "threadId": "t", "turn": {"id": "r", "status": "failed", "items": [], "error": {"message": "limit reached"}}}})
        self.assertEqual(events[-1], {"type": "RUN_ERROR", "threadId": "t", "runId": "r", "message": "limit reached", "code": "TURN_FAILED"})

    def test_interrupt_closes_an_unfinished_message(self):
        mapper = EventMapper()
        mapper.feed({"method": "item/agentMessage/delta", "params": {"threadId": "t", "turnId": "r", "itemId": "a", "delta": "partial"}})
        events = mapper.feed({"method": "turn/completed", "params": {
            "threadId": "t", "turn": {"id": "r", "status": "interrupted", "items": []}}})
        self.assertEqual(events[0]["type"], "TEXT_MESSAGE_END")
        self.assertEqual(events[0]["text"], "partial")
        self.assertEqual(events[1]["metadata"]["status"], "interrupted")
        self.assertNotIn("outcome", events[1])

    def test_tools_have_separate_lifecycle_and_deduplicated_results(self):
        mapper = EventMapper()
        item = {'type': 'dynamicToolCall', 'id': 'tool-1', 'tool': 'remember',
                'arguments': {'text': 'hello'}, 'status': 'inProgress'}
        events = mapper.feed({'method': 'item/started', 'params': {
            'threadId': 't', 'turnId': 'r', 'item': item}})
        item = dict(item, status='completed', success=True,
                    contentItems=[{'type': 'inputText', 'text': 'saved'}])
        events += mapper.feed({'method': 'item/completed', 'params': {
            'threadId': 't', 'turnId': 'r', 'item': item}})
        events += mapper.feed({'method': 'turn/completed', 'params': {
            'threadId': 't', 'turn': {'id': 'r', 'status': 'completed', 'items': [item]}}})
        self.assertEqual([event['type'] for event in events], [
            'RUN_STARTED', 'TOOL_CALL_START', 'TOOL_CALL_ARGS', 'TOOL_CALL_END',
            'STEP_STARTED', 'TOOL_CALL_RESULT', 'STEP_FINISHED', 'RUN_FINISHED'])
        self.assertTrue(all(event['threadId'] == 't' and event['runId'] == 'r' for event in events))
        self.assertTrue(all(event['toolCallId'] == 'tool-1' for event in events[1:-1]))
        self.assertEqual(events[-1]['outcome'], {'type': 'success'})
        self.assertIn('saved', events[-3]['content'])
        wire = to_agui_event(events[-3])
        self.assertNotIn('threadId', wire)
        self.assertEqual(wire['metadata']['threadId'], 't')
        self.assertEqual(wire['metadata']['runId'], 'r')
        self.assertEqual(wire['toolCallId'], 'tool-1')

    def test_image_tool_result_omits_binary_and_private_file_path(self):
        events = EventMapper().feed({'method': 'item/completed', 'params': {
            'threadId': 't', 'turnId': 'r', 'item': {
                'type': 'imageGeneration', 'id': 'image', 'status': 'completed',
                'result': 'private-base64-data', 'savedPath': '/private/image.png'}}})
        self.assertNotIn('private-base64-data', str(events))
        self.assertNotIn('/private/image.png', str(events))
        self.assertIn('imageAvailable', events[-2]['content'])

    def test_event_validation_rejects_invalid_custom_and_unrouted_payloads(self):
        event = {'type': 'CUSTOM', 'threadId': 't', 'runId': 'r', 'name': 'artifact',
                 'value': {'id': 'a', 'name': 'test.png', 'mime': 'image/png',
                           'url': '/artifacts/a', 'caption': ''}}
        self.assertIs(validate_event(event), event)
        for invalid in [dict(event, runId=''), dict(event, name='unknown'),
                        dict(event, value=dict(event['value'], path='/private/a')),
                        dict(event, value=dict(event['value'], caption='x' * 16001)),
                        dict(event, type='UNSUPPORTED'), dict(event, metadata='wrong')]:
            with self.subTest(invalid=invalid.get('type')):
                with self.assertRaises(ValueError):
                    validate_event(invalid)

    def test_subscription_gate_rejects_api_keys_and_missing_login(self):
        for account in [None, {"type": "apiKey"}, {"type": "amazonBedrock"}]:
            with self.assertRaises(RpcError):
                require_chatgpt_account({"account": account})
        require_chatgpt_account({"account": {"type": "chatgpt", "email": "private@example.test", "planType": "plus"}})


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_runtime_model_is_preserved_and_provider_mismatch_is_rejected(self):
        client = RuntimeClient()
        client._authenticated = True
        sent = []
        response = {'model': 'synthetic-choice', 'modelProvider': 'openai', 'thread': {'id': 'history-thread'}}

        async def send(message):
            sent.append(message)
            client._pending[message['id']].set_result(response)

        client._send = send
        for method in ('thread/start', 'thread/resume', 'turn/start'):
            try:
                await client.request(method, {'model': 'synthetic-choice', 'modelProvider': 'untrusted-provider'})
            except RpcError:
                self.fail('An explicitly requested model must be preserved through the runtime transport.')
            self.assertEqual(sent[-1]['params']['model'], 'synthetic-choice')
            if method.startswith('thread/'):
                self.assertEqual(sent[-1]['params']['modelProvider'], 'openai')
        response = {'model': 'gpt-6.1-sol', 'modelProvider': 'openai', 'thread': {'id': 'default-thread'}}
        await client.request('thread/start', {})
        self.assertEqual(sent[-1]['params']['model'], 'gpt-6.1-sol')
        response = {'model': 'synthetic-choice', 'modelProvider': 'untrusted-provider', 'thread': {'id': 'history-thread'}}
        with self.assertRaises(RpcError):
            await client.request('thread/resume', {'model': 'synthetic-choice'})
        with self.assertRaises(RpcError):
            await client.request('thread/start', {'model': ''})

    async def test_cancelled_start_retains_acknowledgement_for_stop(self):
        import asyncio
        client = AsyncMock()
        entered, acknowledge = asyncio.Event(), asyncio.Event()

        async def request(method, params):
            if method == 'turn/start':
                entered.set()
                await acknowledge.wait()
                return {'turn': {'id': 'accepted-turn'}}
            return {}

        client.request.side_effect = request
        controller = Controller(client, ':memory:', '.', AsyncMock())
        self.addCleanup(controller.close)
        controller._input = AsyncMock(return_value=[{'type': 'text', 'text': 'hello'}])
        controller._thread = AsyncMock(return_value='thread')
        controller.threads[7] = 'thread'
        task = asyncio.create_task(controller.submit(7, 'hello', 10))
        await entered.wait()
        task.cancel()
        acknowledge.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(controller.active[7], 'accepted-turn')
        self.assertEqual(controller.db.execute('SELECT status FROM inputs').fetchone()[0], 'accepted')
        self.assertEqual(await controller.stop(7), 'requested')
        client.request.assert_awaited_with('turn/interrupt', {'threadId': 'thread', 'turnId': 'accepted-turn'})

    async def test_cancelled_thread_setup_cannot_replay_pending_input(self):
        import asyncio
        entered = asyncio.Event()

        async def thread(_):
            entered.set()
            await asyncio.Event().wait()

        controller = Controller(AsyncMock(), ':memory:', '.', AsyncMock())
        self.addCleanup(controller.close)
        controller._input = AsyncMock(return_value=[])
        controller._thread = thread
        task = asyncio.create_task(controller.submit(7, 'hello', 10))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(controller.db.execute('SELECT status FROM inputs').fetchone()[0], 'cancelled')
        controller.submit = AsyncMock()
        await controller.recover()
        controller.submit.assert_not_awaited()

    async def test_dynamic_tool_callback_receives_routing_and_returns_native_content(self):
        result = {"success": True, "contentItems": [{"type": "inputText", "text": "saved"}]}
        handler = AsyncMock(return_value=result)
        client = RuntimeClient(tool_handler=handler)
        client._send = AsyncMock()
        await client._handle_server_request({"id": 7, "method": "item/tool/call", "params": {
            "threadId": "t", "turnId": "r", "callId": "call", "tool": "remember", "arguments": {"text": "hello"}}})
        handler.assert_awaited_once_with({"requestId": 7, "method": "item/tool/call", "threadId": "t",
                                        "turnId": "r", "callId": "call", "tool": "remember", "arguments": {"text": "hello"}})
        client._send.assert_awaited_once_with({"id": 7, "result": result})

    async def test_approval_failure_declines_without_leaking_handler_details(self):
        client = RuntimeClient(approval_handler=AsyncMock(side_effect=RuntimeError("private handler details")))
        client._send = AsyncMock()
        await client._handle_server_request({"id": 8, "method": "item/fileChange/requestApproval", "params": {
            "threadId": "t", "turnId": "r", "itemId": "file"}})
        client._send.assert_awaited_once_with({"id": 8, "result": {"decision": "decline"}})
        self.assertNotIn("private", str(client.events.get_nowait()))

    async def test_waiting_for_approval_does_not_block_rpc_responses(self):
        import asyncio
        from types import SimpleNamespace
        entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def approval(_):
            entered.set()
            try:
                await release.wait()
                return {"decision": "decline"}
            finally:
                exited.set()

        client = RuntimeClient(approval_handler=approval)
        stream = asyncio.StreamReader()
        client.process = SimpleNamespace(stdout=stream)
        client._send = AsyncMock()
        future = asyncio.get_running_loop().create_future()
        client._pending[99] = future
        reader = asyncio.create_task(client._read_loop())
        try:
            stream.feed_data(b'{"id":1,"method":"item/fileChange/requestApproval","params":{}}\n')
            await asyncio.wait_for(entered.wait(), 1)
            stream.feed_data(b'{"id":99,"result":{"interrupted":true}}\n')
            self.assertEqual(await asyncio.wait_for(future, 1), {"interrupted": True})
            stream.feed_data(b'{"method":"serverRequest/resolved","params":{"requestId":1,"threadId":"t"}}\n')
            await asyncio.wait_for(exited.wait(), 1)
            client._send.assert_not_awaited()
        finally:
            release.set()
            stream.feed_eof()
            await reader

    async def test_stop_tolerates_activation_and_completion_races(self):
        client = AsyncMock()
        controller = Controller(client, ':memory:', '.', AsyncMock())
        self.addCleanup(controller.close)
        controller.active[1], controller.threads[1] = 'r', 't'
        client.request.side_effect = [RpcError(-1, 'no active turn to interrupt'), {}]
        with patch('oak.controller.asyncio.sleep', new_callable=AsyncMock):
            self.assertEqual(await controller.stop(1), 'requested')
            client.request.side_effect = RpcError(-1, 'no active turn to interrupt')
            self.assertEqual(await controller.stop(1), 'not_active')
        self.assertEqual(controller.active[1], 'r')

    async def test_unconfirmed_steer_never_starts_duplicate_turn(self):
        client = AsyncMock()
        controller = Controller(client, ':memory:', '.', AsyncMock())
        self.addCleanup(controller.close)
        controller._thread = AsyncMock(return_value='t')
        controller.active[1] = 'r'
        client.request.side_effect = [RpcError(-1, 'no active turn')] * 6 + [
            {'thread': {'turns': [{'id': 'r', 'status': 'inProgress'}]}}]
        with patch('oak.controller.asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaises(RuntimeError):
                await controller.submit(1, 'correction', 1)
        self.assertNotIn('turn/start', [call.args[0] for call in client.request.await_args_list])
        self.assertEqual(controller.db.execute('SELECT status FROM inputs').fetchone()[0], 'uncertain')

    async def test_execution_rejected_before_authentication(self):
        client = RuntimeClient()
        for method in ("thread/start", "thread/resume", "turn/start", "turn/steer"):
            with self.assertRaises(RpcError):
                await client.request(method, {})

    async def test_approval_callbacks_never_grant_permission(self):
        client = RuntimeClient()
        client._send = AsyncMock()
        for method, result in [
            ("item/commandExecution/requestApproval", {"decision": "decline"}),
            ("item/fileChange/requestApproval", {"decision": "decline"}),
            ("item/permissions/requestApproval", {"permissions": {}, "scope": "turn"}),
            ("item/tool/requestUserInput", {"answers": {}}),
            ("mcpServer/elicitation/request", {"action": "decline"}),
        ]:
            await client._handle_server_request({"id": 55, "method": method, "params": {"threadId": "t", "turnId": "r"}})
            client._send.assert_awaited_with({"id": 55, "result": result})
            notice = client.events.get_nowait()
            self.assertEqual(notice["method"], "client/requestDenied")
            self.assertEqual(notice["params"]["turnId"], "r")


if __name__ == "__main__":
    unittest.main()
