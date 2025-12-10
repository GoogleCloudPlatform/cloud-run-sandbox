# Client Reconnection

This document describes the client reconnection feature, which allows a client to automatically reconnect to a sandbox session if the WebSocket connection is unexpectedly closed.

## Overview

The client reconnection feature is designed to improve the resilience of the sandbox sessions. If a client's WebSocket connection is dropped due to a network issue or other transient failure, the client will attempt to automatically reconnect to the same sandbox session, preserving the state of the sandbox and any running processes. This feature is controlled by the `enableAutoReconnect` option in the `Sandbox.create` and `Sandbox.attach` methods.

## Session Affinity

For the reconnection feature to work reliably, it is crucial that the client reconnects to the same Cloud Run instance that is managing the sandbox session. This is achieved through session affinity, which is a feature of Cloud Run that routes requests from the same client to the same container instance.

When a client first connects to a Cloud Run service with session affinity enabled, the service returns a `GAESA` cookie. The Cloud Run Sandbox client automatically captures this cookie and includes it in all subsequent requests. This ensures that if the connection is dropped and the client needs to reconnect, the reconnection request will be routed to the correct Cloud Run instance, allowing the client to resume its session.

To enable session affinity for your Cloud Run service, use the `--session-affinity` flag when deploying your service:

```bash
gcloud run deploy YOUR_SERVICE_NAME --source . --session-affinity
```

## The `Connection` Class

The `Connection` class in `clients/js/src/connection.ts` is a wrapper around the `ws` WebSocket library that provides the core reconnection logic. It handles the following:

- **Automatic Reconnection:** When a WebSocket connection is closed, the `Connection` class consults a `shouldReconnect` callback to determine if it should attempt to reconnect.
- **Reconnection Information:** If a reconnection is necessary, the `Connection` class calls a `getReconnectInfo` callback to get the URL and options for the new connection. This allows the client to reconnect to the same sandbox session.
- **Reopen Event:** When a connection is re-established, the `Connection` class emits a `reopen` event, which the `Sandbox` class uses to send a `reconnect` action to the server.

## The `Sandbox` Class

The `Sandbox` class in `clients/js/src/sandbox.ts` is updated to use the `Connection` class and manage the reconnection state.

### `enableAutoReconnect` Option

The `enableAutoReconnect` option in the `Sandbox.create` and `Sandbox.attach` methods is a boolean flag that controls whether the client should automatically reconnect to the sandbox session if the connection is dropped. When this option is set to `true`, the `_shouldReconnect` property of the `Sandbox` instance is set to `true` when the sandbox is in the `running` state.

### `reconnecting` State

A new `reconnecting` state has been added to the `Sandbox` class. When the `shouldReconnect` callback is called and returns `true`, the sandbox's state is set to `reconnecting`. This prevents any new operations from being started until the connection is re-established.

### stdin Buffering

While the sandbox is in the `reconnecting` state, any stdin messages sent to a running process are buffered in a `stdinBuffer`. Once the connection is re-established and the sandbox returns to the `running` state, the buffered stdin messages are flushed to the server, ensuring that no user input is lost.

## Server-Side Handling

The `WebsocketHandler` in `src/handlers/websocket.py` has a new `reconnect_and_stream` method to handle the `reconnect` action from the client. When this action is received, the server resumes streaming any buffered output from the sandbox to the newly connected client.
