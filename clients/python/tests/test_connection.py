
import asyncio
import unittest
import websockets
from websockets.frames import Close
from unittest.mock import AsyncMock, MagicMock, patch

from sandbox.connection import Connection

class TestConnection(unittest.TestCase):
    @patch('websockets.connect', new_callable=AsyncMock)
    def test_successful_connection_and_messaging(self, mock_connect):
        """
        Verify that the Connection class can connect, send, and receive messages.
        """
        async def run_test():
            mock_ws = AsyncMock()
            mock_ws.state = websockets.protocol.State.OPEN
            mock_ws.recv.side_effect = ["message1", "message2", websockets.exceptions.ConnectionClosed(rcvd=Close(code=1000, reason="Normal"), sent=None)]
            mock_connect.return_value = mock_ws

            on_message_mock = MagicMock()
            on_error_mock = MagicMock()
            on_close_mock = MagicMock()

            conn = Connection(
                url="ws://fake-url",
                on_message=on_message_mock,
                on_error=on_error_mock,
                on_close=on_close_mock,
                should_reconnect=MagicMock(return_value=False),
                get_reconnect_info=lambda: {},
                on_reopen=AsyncMock(),
            )
            
            connect_task = asyncio.create_task(conn.connect())
            await asyncio.sleep(0.01)

            self.assertEqual(on_message_mock.call_count, 2)
            on_message_mock.assert_any_call("message1")
            on_message_mock.assert_any_call("message2")

            await connect_task
            on_close_mock.assert_called_once_with(1000, "Normal")
            on_error_mock.assert_not_called()

        asyncio.run(run_test())

    def test_connection_failure(self):
        """
        Verify that the on_error callback is triggered on connection failure.
        """
        async def run_test():
            connection_error = ConnectionRefusedError("Connection refused")
            with patch('websockets.connect', new_callable=AsyncMock) as mock_connect:
                mock_connect.side_effect = connection_error

                on_message_mock = MagicMock()
                on_error_mock = MagicMock()
                on_close_mock = MagicMock()

                conn = Connection(
                    url="ws://fake-url",
                    on_message=on_message_mock,
                    on_error=on_error_mock,
                    on_close=on_close_mock,
                    should_reconnect=MagicMock(return_value=False),
                    get_reconnect_info=lambda: {},
                    on_reopen=AsyncMock(),
                )

                with self.assertRaises(ConnectionRefusedError):
                    await conn.connect()

                on_error_mock.assert_called_once_with(connection_error)
                on_message_mock.assert_not_called()
                on_close_mock.assert_not_called()

        asyncio.run(run_test())

    @patch('websockets.connect', new_callable=AsyncMock)
    def test_successful_reconnection(self, mock_connect):
        """
        Verify that the Connection class attempts to reconnect if should_reconnect is true.
        """
        async def run_test():
            ws1 = AsyncMock()
            ws1.recv.side_effect = [websockets.exceptions.ConnectionClosed(rcvd=Close(code=1006, reason="Abnormal"), sent=None)]
            
            ws2 = AsyncMock()
            ws2.recv.side_effect = [asyncio.CancelledError] # To stop the loop

            mock_connect.side_effect = [ws1, ws2]

            should_reconnect_mock = MagicMock(return_value=True)
            on_reopen_mock = AsyncMock()
            on_close_mock = MagicMock()

            conn = Connection(
                url="ws://fake-url",
                on_message=MagicMock(),
                on_error=MagicMock(),
                on_close=on_close_mock,
                should_reconnect=should_reconnect_mock,
                get_reconnect_info=lambda: {"url": "ws://reconnect-url"},
                on_reopen=on_reopen_mock,
            )
            
            await conn.connect()
            await asyncio.sleep(0.01) # Allow time for reconnect task to start
            
            should_reconnect_mock.assert_called_once_with(1006, "Abnormal")
            self.assertEqual(mock_connect.call_count, 2)
            on_reopen_mock.assert_awaited_once()
            on_close_mock.assert_not_called()

            conn._listen_task.cancel()

        asyncio.run(run_test())

    @patch('websockets.connect', new_callable=AsyncMock)
    def test_on_close_called_when_should_reconnect_is_false(self, mock_connect):
        """
        Verify that on_close is called when should_reconnect returns false.
        """
        async def run_test():
            ws = AsyncMock()
            ws.recv.side_effect = [websockets.exceptions.ConnectionClosed(rcvd=Close(code=1001, reason="Going away"), sent=None)]
            mock_connect.return_value = ws

            should_reconnect_mock = MagicMock(return_value=False)
            on_close_mock = MagicMock()

            conn = Connection(
                url="ws://fake-url",
                on_message=MagicMock(),
                on_error=MagicMock(),
                on_close=on_close_mock,
                should_reconnect=should_reconnect_mock,
                get_reconnect_info=lambda: {},
                on_reopen=AsyncMock(),
            )

            await conn.connect()
            await asyncio.sleep(0.01)

            should_reconnect_mock.assert_called_once_with(1001, "Going away")
            on_close_mock.assert_called_once_with(1001, "Going away")
            self.assertEqual(mock_connect.call_count, 1)

        asyncio.run(run_test())

    @patch('websockets.connect', new_callable=AsyncMock)
    def test_reconnection_failure(self, mock_connect):
        """
        Verify that a reconnection failure calls on_error and on_close.
        """
        async def run_test():
            ws1 = AsyncMock()
            ws1.recv.side_effect = [websockets.exceptions.ConnectionClosed(rcvd=Close(code=1006, reason="Abnormal"), sent=None)]
            
            reconnect_error = ConnectionRefusedError("Reconnect failed")
            mock_connect.side_effect = [ws1, reconnect_error]

            should_reconnect_mock = MagicMock(return_value=True)
            on_error_mock = MagicMock()
            on_close_mock = MagicMock()
            on_reopen_mock = AsyncMock() # Define on_reopen_mock here

            conn = Connection(
                url="ws://fake-url",
                on_message=MagicMock(),
                on_error=on_error_mock,
                on_close=on_close_mock,
                should_reconnect=should_reconnect_mock,
                get_reconnect_info=lambda: {"url": "ws://reconnect-url"},
                on_reopen=on_reopen_mock,
            )

            await conn.connect()
            await asyncio.sleep(0.01)

            should_reconnect_mock.assert_called_once_with(1006, "Abnormal")
            self.assertEqual(mock_connect.call_count, 2)
            on_error_mock.assert_called_once_with(reconnect_error)
            on_close_mock.assert_called_once_with(1011, str(reconnect_error))

        asyncio.run(run_test())

    @patch('websockets.connect', new_callable=AsyncMock)
    def test_intentional_close(self, mock_connect):
        """
        Verify that an intentional close does not trigger a reconnect.
        """
        async def run_test():
            mock_ws = AsyncMock()
            mock_ws.state = websockets.protocol.State.OPEN
            mock_connect.return_value = mock_ws

            should_reconnect_mock = MagicMock(return_value=True)
            on_close_mock = MagicMock()

            conn = Connection(
                url="ws://fake-url",
                on_message=MagicMock(),
                on_error=MagicMock(),
                on_close=on_close_mock,
                should_reconnect=should_reconnect_mock,
                get_reconnect_info=lambda: {},
                on_reopen=AsyncMock(),
            )

            await conn.connect()
            await conn.close()

            self.assertTrue(conn.is_closed_intentionally)
            mock_ws.close.assert_called_once()
            
            await asyncio.sleep(0.01)
            
            should_reconnect_mock.assert_not_called()
            on_close_mock.assert_not_called()

        asyncio.run(run_test())

if __name__ == '__main__':
    unittest.main()
