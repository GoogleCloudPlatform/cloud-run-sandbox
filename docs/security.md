# Cloud Run Sandbox Security

This document outlines the security mechanisms implemented in the Cloud Run Sandbox service to ensure a safe and isolated environment for executing untrusted code.

## Overview

The Cloud Run Sandbox employs a multi-layered security model to protect the underlying infrastructure and prevent unauthorized access to sandbox instances. This approach combines the inherent security features of the Google Cloud Run environment with application-level sandboxing using gVisor and a robust authentication token system. Each layer provides a distinct boundary of protection, creating a strong defense-in-depth posture.

## Layered Security Mechanisms

The Cloud Run Sandbox employs a multi-layered security approach to ensure a secure execution environment.

1.  **Cloud Run Gen2 Environment**: The entire sandbox service operates within the Google Cloud Run second-generation execution environment. This provides a foundational layer of security, as the service itself is sandboxed and isolated from other workloads. For more details on the security features of Cloud Run, see the official documentation: [https://docs.cloud.google.com/run/docs/securing/security](https://docs.cloud.google.com/run/docs/securing/security).

2.  **gVisor Sandbox**: Within the Cloud Run service, we create a dedicated gVisor sandbox for running the actual untrusted user code. gVisor intercepts application system calls and acts as a guest kernel, isolating the application and reducing its attack surface. This layer is also what enables advanced features like checkpointing and filesystem snapshots.

3.  **Sandbox Authentication Token**: This token mechanism adds a crucial authentication layer. It ensures that only the client that originally created the sandbox can re-attach to it, preventing unauthorized access even if a sandbox ID is known.

### Security Layers Diagram

The following diagram illustrates these layers:

```
+-----------------------------------------------------------------+
|  Google Cloud Run (Gen2) Execution Environment                  |
|  (Provides foundational sandboxing and isolation)               |
|                                                                 |
|  +-----------------------------------------------------------+  |
|  | WebSocket Server (Cloud Run Sandbox)                      |  |
|  | - Validates client token on `attach` requests             |  |
|  | - Manages gVisor sandbox lifecycle                        |  |
|  |                                                           |  |
|  | +-------------------------------------------------------+ |  |
|  | |  gVisor Sandbox (runsc)                               | |  |
|  | |  (Isolates untrusted code execution)                  | |  |
|  | |                                                       | |  |
|  | |  +-------------------------------------------------+  | |  |
|  | |  | Filesystem                                      |  | |  |
|  | |  |                                                 |  | |  |
|  | |  |  /tmp/sandbox_token  [Contains secret token]    |  | |  |
|  | |  |                                                 |  | |  |
|  | |  +-------------------------------------------------+  | |  |
|  | |                                                       | |  |
|  | +-------------------------------------------------------+ |  |
|  +-----------------------------------------------------------+  |
|                                                                 |
+-----------------------------------------------------------------+
```

## Sandbox Token: Server-Side Implementation

### Token Generation

-   When a new sandbox is created, the server generates a unique, cryptographically secure token.
-   The token is stored by writing it to a file at `/tmp/sandbox_token` inside the sandbox's filesystem. This is done by executing a shell command within the sandbox (`runsc exec ... echo -n '{token}' > /tmp/sandbox_token`).
-   When a sandbox is checkpointed, this file is saved as part of the gVisor filesystem state. Upon restoration, the file is present, allowing the server to retrieve and verify the token by executing `cat /tmp/sandbox_token`.

### Token Validation

-   The `attach` endpoint (`/attach/{sandbox_id}`) has been updated to require a `sandbox_token` as a query parameter.
-   When a client attempts to attach, the server validates the provided token against the one stored with the sandbox instance.
-   If the token is valid, the connection is upgraded, and the client is allowed to attach.
-   If the token is invalid or missing, the server rejects the connection.

### Permission Denial Event

-   In the event of a token mismatch, the server sends a `SANDBOX_PERMISSION_DENIAL_ERROR` event to the client over the WebSocket connection before closing it.
-   This is a fatal error, indicating that the client is not authorized. It prevents any automatic reconnection attempts, as they would also fail.

## Sandbox Token: Client-Side Implementation

### Receiving and Storing the Token

-   When a client successfully creates a sandbox using `Sandbox.create()`, the server returns the `sandbox_id` and the newly generated `sandbox_token`.
-   The client library is responsible for storing both of these values. The `sandbox_token` is now available as a property on the `Sandbox` instance (e.g., `sandbox.sandboxToken`).

### Sending the Token on Attach

-   The `Sandbox.attach()` method signature has been updated across all client libraries (Python and JavaScript/TypeScript) to include the `sandbox_token`.
-   When attaching to an existing sandbox, the client must provide the `sandbox_id` and the corresponding `sandbox_token`.
-   The client library automatically includes the token in the WebSocket URL when making the `attach` request.

### Handling Permission Errors

-   The client libraries have been updated to recognize the `SANDBOX_PERMISSION_DENIAL_ERROR` event.
-   Upon receiving this event, the client will raise a specific exception (e.g., `SandboxPermissionError` or a `SandboxCreationError` with a specific message) to inform the user that the attachment was denied due to an authorization failure.
-   This error is treated as fatal, and the client will not attempt to auto-reconnect.
