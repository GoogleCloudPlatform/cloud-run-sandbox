
import asyncio
import websockets
from typing import Callable, Any, Dict, Optional, Awaitable

ShouldReconnectCallback = Callable[[int, str], bool]

class ReconnectInfo:
    url: str
    ws_options: Optional[Dict[str, Any]]

GetReconnectInfoCallback = Callable[[], ReconnectInfo]

class Connection:
    """
    Manages the lifecycle of a WebSocket connection, handling the raw
    communication and delegating events to the consumer via callbacks.
    """
    def __init__(
        self,
        url: str,
        on_message: Callable[[str], Any],
        on_error: Callable[[Exception], Any],
        on_close: Callable[[int, str], Any],
        should_reconnect: ShouldReconnectCallback,
        get_reconnect_info: GetReconnectInfoCallback,
        on_reopen: Callable[[], Awaitable[None]],
        ws_options: Optional[Dict[str, Any]] = None,
        debug: bool = False,
        debug_label: str = '',
    ):
        self.url = url
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.should_reconnect = should_reconnect
        self.get_reconnect_info = get_reconnect_info
        self.on_reopen = on_reopen
        self.ws_options = ws_options or {}
        self._debug_enabled = debug
        self._debug_label = debug_label
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._listen_task: Optional[asyncio.Task] = None
        self.is_closed_intentionally = False
        self.is_reconnecting = False
        self.cookie: Optional[str] = None

    def _log_debug(self, message, *args):
        if self._debug_enabled:
            print(f"[{self._debug_label}] [DEBUG] {message}", *args)

    def _get_cookie_from_headers(self, headers):
        if "Set-Cookie" in headers:
            return headers["Set-Cookie"]
        return None

    async def _establish_connection(self, url, options):
        self._log_debug(f"Establishing connection to {url}")
        
        headers = {}
        if self.cookie:
            self._log_debug(f"Using existing cookie for session affinity: {self.cookie}")
            headers['Cookie'] = self.cookie

        ws = await websockets.connect(
            url,
            additional_headers=headers,
            **options
        )
        
        new_cookie = self._get_cookie_from_headers(ws.response.headers)
        if new_cookie:
            self.cookie = new_cookie
            self._log_debug(f"Captured session affinity cookie: {self.cookie}")
        
        self._log_debug("Connection established.")
        return ws

    async def connect(self):
        """
        Establishes the WebSocket connection and starts the listener task.
        """
        self._log_debug(f"Connecting to {self.url}")
        try:
            self.ws = await self._establish_connection(self.url, self.ws_options)
            self._listen_task = asyncio.create_task(self._listen())
        except Exception as e:
            self._log_debug(f"Connection failed: {e}")
            self.on_error(e)
            raise

    async def _listen(self):
        """
        Listens for incoming messages and handles connection state changes.
        """
        while True:
            try:
                message = await self.ws.recv()
                self.on_message(message)
            except websockets.exceptions.ConnectionClosed as e:
                if e.rcvd:
                    self._log_debug(f"Connection closed gracefully: {e.rcvd.code} {e.rcvd.reason}")
                    close_code, close_reason = e.rcvd.code, e.rcvd.reason
                else:
                    self._log_debug(f"Connection closed abruptly: {e}")
                    close_code, close_reason = 1006, "Abnormal Closure"

                if self.is_closed_intentionally:
                    self._log_debug("Connection closed intentionally. Not reconnecting.")
                    self.on_close(close_code, close_reason)
                    break

                if not self.should_reconnect(close_code, close_reason):
                    self._log_debug("`should_reconnect` returned false. Not reconnecting.")
                    self.on_close(close_code, close_reason)
                    break
                
                self._log_debug("`should_reconnect` returned true. Attempting to reconnect.")
                try:
                    await self._reconnect()
                    self._log_debug("Resuming listening on new connection.")
                    continue # Resume listening
                except Exception as reconnect_e:
                    self._log_debug(f"Reconnect attempt failed: {reconnect_e}")
                    self.on_error(reconnect_e)
                    self.on_close(1011, str(reconnect_e))
                    break
            except Exception as e:
                self._log_debug(f"An error occurred in listener: {e}")
                self.on_error(e)
                self.on_close(1011, str(e))
                break

    async def _reconnect(self):
        self.is_reconnecting = True
        reconnect_info = self.get_reconnect_info()
        self.url = reconnect_info['url']
        self.ws_options = reconnect_info.get('ws_options', self.ws_options)
        self._log_debug(f"Reonnecting to {self.url}")
        self.ws = await self._establish_connection(self.url, self.ws_options)
        self.is_reconnecting = False
        if self.on_reopen:
            await self.on_reopen()

    async def send(self, data: str):
        """
        Sends a message over the WebSocket connection.
        """
        if self.ws and self.ws.state != websockets.protocol.State.CLOSED: 
            await self.ws.send(data)
        else:
            raise ConnectionError("WebSocket is not connected.")

    async def close(self, code: int = 1000, reason: str = ""):
        """
        Closes the WebSocket connection intentionally.
        """
        if self.is_closed_intentionally:
            return
        self.is_closed_intentionally = True
        
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()

        if self.ws and self.ws.state != websockets.protocol.State.CLOSED:
            self._log_debug("Closing WebSocket connection intentionally.")
            await self.ws.close(code, reason)
