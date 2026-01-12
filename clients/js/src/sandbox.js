/**
 * Copyright 2025 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import WebSocket from 'ws';
import { EventEmitter } from 'events';
import { GoogleAuth } from 'google-auth-library';
import { MessageKey, EventType, SandboxEvent } from './types.js';
import { SandboxProcess } from './process.js';
import { Connection } from './connection.js';

/**
 * @typedef {'creating' | 'running' | 'closed' | 'failed' | 'checkpointing' | 'checkpointed' | 'restoring' | 'filesystem_snapshotting' | 'reconnecting'} SandboxState
 */

export class Sandbox {
  static get HINT_AUTH_PERMISSION() {
    return " (Hint: Permission denied or missing authentication. Ensure you have enabled 'useGoogleAuth: true' in the Sandbox.create/attach options, and that your account has the 'Cloud Run Invoker' (roles/run.invoker) role on this service.)";
  }

  static get TROUBLESHOOTING_URL() {
    return "https://docs.cloud.google.com/run/docs/troubleshooting";
  }

  /**
   * @param {Connection} connection
   * @param {boolean} [debug]
   * @param {string} [debugLabel]
   * @param {boolean} [autoReconnectEnabled]
   * @param {boolean} [useGoogleAuth]
   */
  constructor(connection, debug = false, debugLabel = '', autoReconnectEnabled = false, useGoogleAuth = false) {
    this.connection = connection;
    this.eventEmitter = new EventEmitter();
    this._sandboxId = null;
    this._sandboxToken = null;
    this._creationError = null;
    /** @type {SandboxProcess | null} */
    this.activeProcess = null;
    /** @type {SandboxState} */
    this.state = 'creating';
    this._debugEnabled = debug;
    this._debugLabel = debugLabel;
    this._shouldReconnect = false;
    this._autoReconnectEnabled = autoReconnectEnabled;
    this._isCheckpointIntentionally = false;
    this._isKillIntentionally = false;
    this._url = '';
    this._wsOptions = undefined;
    this._useGoogleAuth = useGoogleAuth;
    /** @type {string[]} */
    this.stdinBuffer = [];

    this.connection.on('message', this.handleMessage.bind(this));
    this.connection.on('close', this.handleClose.bind(this));
    this.connection.on('error', this.handleError.bind(this));
    this.connection.on('reopen', this.handleReopen.bind(this));
  }

  /**
   * @returns {string | null}
   */
  get sandboxId() {
    return this._sandboxId;
  }

  /**
   * @returns {string | null}
   */
  get sandboxToken() {
    return this._sandboxToken;
  }

  /**
   * @param {string} message
   * @param {...any} args
   */
  logDebugMessage(message, ...args) {
    if (this._debugEnabled) {
      console.log(`[${this._debugLabel}] [DEBUG] ${message}`, ...args);
    }
  }

  /**
   * @param {any} error
   * @returns {string}
   */
  static appendErrorHint(error) {
    let msg = error instanceof Error ? error.message : String(error);
    if (msg.includes('401') || msg.includes('403')) {
      msg += this.HINT_AUTH_PERMISSION;
    }
    if (msg.includes('HTTP')) {
      msg += ` For more troubleshooting steps, see: ${this.TROUBLESHOOTING_URL}`;
    }
    return msg;
  }

  /**
   * @param {string} url
   * @returns {Promise<string>}
   */
  static async getIdToken(url) {
    const auth = new GoogleAuth();
    // Always use https protocol for audience generation, as Cloud Run OIDC audiences are HTTPS.
    const urlObj = new URL(url);
    urlObj.protocol = 'https:';
    const audience = urlObj.origin;
    const client = await auth.getIdTokenClient(audience);
    const clientHeaders = await client.getRequestHeaders();
    const authHeader = clientHeaders['Authorization'];
    if (typeof authHeader === 'string') {
      return authHeader.replace('Bearer ', '');
    }
    throw new Error('Could not fetch ID token.');
  }

  /**
   * @param {string} status
   */
  _updateShouldReconnect(status) {
    const isFatalError = [
      SandboxEvent.SANDBOX_ERROR,
      SandboxEvent.SANDBOX_NOT_FOUND,
      SandboxEvent.SANDBOX_CREATION_ERROR,
      SandboxEvent.SANDBOX_CHECKPOINT_ERROR,
      SandboxEvent.SANDBOX_RESTORE_ERROR,
      SandboxEvent.SANDBOX_DELETED,
      SandboxEvent.SANDBOX_LOCK_RENEWAL_ERROR,
      SandboxEvent.SANDBOX_PERMISSION_DENIAL_ERROR,
    ].includes(status);

    if (isFatalError) {
      this._shouldReconnect = false;
    } else if (status === SandboxEvent.SANDBOX_RUNNING) {
      this._shouldReconnect = this._autoReconnectEnabled;
    }
  }

  /**
   * @param {import('ws').Data} data
   */
  handleMessage(data) {
    /** @type {import('./types.js').WebSocketMessage} */
    const message = JSON.parse(data.toString());

    if (message.event != EventType.STDOUT && message.event != EventType.STDERR) {
      this.logDebugMessage('Received message:', message);
    }

    // Process-specific events are always forwarded
    if (
      message.event === EventType.STDOUT ||
      message.event === EventType.STDERR ||
      (message.event === EventType.STATUS_UPDATE &&
        // @ts-ignore
        message.status &&
        // @ts-ignore
        message.status.startsWith('SANDBOX_EXECUTION_') &&
        // @ts-ignore
        message.status !== SandboxEvent.SANDBOX_EXECUTION_IN_PROGRESS_ERROR)
    ) {
      if (this.activeProcess) {
        this.activeProcess.handleMessage(message);
      }
      return;
    }

    // Handle sandbox lifecycle events
    if (message.event === EventType.SANDBOX_ID) {
      // @ts-ignore
      this._sandboxId = message.sandbox_id;
      // @ts-ignore
      this._sandboxToken = message.sandbox_token ?? null;
      return;
    }

    if (message.event === EventType.STATUS_UPDATE) {
      // @ts-ignore
      this._updateShouldReconnect(message.status);

      // @ts-ignore
      switch (message.status) {
        case SandboxEvent.SANDBOX_RUNNING:
          if (this.state === 'reconnecting') {
            this.flushStdinBuffer();
          }
          this.state = 'running';
          this.eventEmitter.emit('created', this);
          break;
        case SandboxEvent.SANDBOX_CREATION_ERROR:
          if (this.state === 'creating') {
            this.state = 'failed';
            // @ts-ignore
            this._creationError = new Error(message.message || message.status);
            this.eventEmitter.emit('failed', this._creationError);
          }
          break;
        case SandboxEvent.SANDBOX_PERMISSION_DENIAL_ERROR:
          this.state = 'failed';
          // @ts-ignore
          this._creationError = new Error(message.message || message.status);
          this.eventEmitter.emit('failed', this._creationError);
          break;
        case SandboxEvent.SANDBOX_CHECKPOINTING:
          this.state = 'checkpointing';
          break;
        case SandboxEvent.SANDBOX_CHECKPOINTED:
          this.state = 'checkpointed';
          this.eventEmitter.emit('checkpointed');
          break;
        case SandboxEvent.SANDBOX_CHECKPOINT_ERROR:
          this.state = 'failed'; // This is a fatal error for the session.
          // @ts-ignore
          this.eventEmitter.emit('checkpoint_error', new Error(message.message || message.status));
          break;
        case SandboxEvent.SANDBOX_EXECUTION_IN_PROGRESS_ERROR:
          this.state = 'running'; // Checkpoint failed, back to running
          // @ts-ignore
          this.eventEmitter.emit('checkpoint_error', new Error(message.message || message.status));
          break;
        case SandboxEvent.SANDBOX_RESTORING:
          this.state = 'restoring';
          break;
        case SandboxEvent.SANDBOX_RESTORE_ERROR:
          this.state = 'failed';
          // @ts-ignore
          this._creationError = new Error(message.message || message.status);
          this.eventEmitter.emit('failed', this._creationError);
          break;
        case SandboxEvent.SANDBOX_NOT_FOUND:
          this.state = 'failed';
          // @ts-ignore
          this._creationError = new Error(message.message || message.status);
          this.eventEmitter.emit('failed', this._creationError);
          break;
        case SandboxEvent.SANDBOX_FILESYSTEM_SNAPSHOT_CREATING:
          this.state = 'filesystem_snapshotting';
          break;
        case SandboxEvent.SANDBOX_FILESYSTEM_SNAPSHOT_CREATED:
          this.state = 'running';
          this.eventEmitter.emit('filesystem_snapshot_created');
          break;
        case SandboxEvent.SANDBOX_FILESYSTEM_SNAPSHOT_ERROR:
          this.state = 'running';
          // @ts-ignore
          this.eventEmitter.emit('filesystem_snapshot_error', new Error(message.message || message.status));
          break;
        case SandboxEvent.SANDBOX_KILLED:
        case SandboxEvent.SANDBOX_KILL_ERROR:
          this.eventEmitter.emit('killed');
          break;
      }
      return;
    }
  }

  /**
   * @param {number} code
   * @param {Buffer} reason
   */
  handleClose(code, reason) {
    const reasonStr = reason ? reason.toString() : 'No reason';
    this.logDebugMessage(`Connection closed: code=${code}, reason=${reasonStr}`);
    
    if (this.state === 'creating' || this.state === 'restoring' || this.state === 'reconnecting') {
      this.state = 'failed';
      const err = new Error(`Connection closed during creation/restoration: code=${code}, reason=${reasonStr}`);
      const enhancedMsg = Sandbox.appendErrorHint(err);
      this.eventEmitter.emit('failed', new Error(enhancedMsg));
    } else if (this.state !== 'failed') {
      this.state = 'closed';
    }
    
    if (this.activeProcess) {
      this.activeProcess.close();
      this.activeProcess = null;
    }
  }

  /**
   * @param {Error} err
   */
  handleError(err) {
    const enhancedMsg = Sandbox.appendErrorHint(err);
    const enhancedError = new Error(enhancedMsg);

    if (this._debugEnabled) {
      console.error(`[${this._debugLabel}] [DEBUG] WebSocket error:`, enhancedError);
    }
    if (this.state === 'creating' || this.state === 'restoring' || this.state === 'reconnecting') {
      this.state = 'failed';
      this.eventEmitter.emit('failed', enhancedError);
    }
    
    if (this.activeProcess) {
      this.activeProcess.close();
      this.activeProcess = null;
    }
  }

  handleReopen() {
    this.logDebugMessage('Reconnected. Sending reconnect action.');
    this.connection.send(JSON.stringify({ action: 'reconnect' }));
  }

  /**
   * @param {number} code
   * @param {Buffer} reason
   * @returns {boolean}
   */
  shouldReconnect(code, reason) {
    const decision = this._shouldReconnect && !this._isCheckpointIntentionally;
    this.logDebugMessage(`Checking if should reconnect: code=${code}, reason=${reason.toString()}, decision=${decision}`);
    if (decision) {
      this.state = 'reconnecting';
    }
    return decision;
  }

  /**
   * @returns {import('./connection.js').ReconnectInfo}
   */
  getReconnectInfo() {
    if (!this._sandboxId) {
      throw new Error('Cannot reconnect without a sandbox ID.');
    }
    const sanitizedUrl = this._url.replace(/\/$/, '');
    const reconnectUrl = `${sanitizedUrl}/attach/${this._sandboxId}?sandbox_token=${this._sandboxToken}`;
    return { url: reconnectUrl, wsOptions: this._wsOptions };
  }

  /**
   * @param {string} url
   * @param {{ idleTimeout?: number, enableSandboxCheckpoint?: boolean, enableSandboxHandoff?: boolean, filesystemSnapshotName?: string, enableDebug?: boolean, debugLabel?: string, wsOptions?: import('ws').ClientOptions, enableAutoReconnect?: boolean, enableIdleTimeoutAutoCheckpoint?: boolean, useGoogleAuth?: boolean }} [options]
   * @returns {Promise<Sandbox>}
   */
  static async create(url, options = {}) {
    const { idleTimeout = 60, enableSandboxCheckpoint = false, enableSandboxHandoff = false, filesystemSnapshotName, enableDebug = false, debugLabel = '', wsOptions, enableAutoReconnect = false, enableIdleTimeoutAutoCheckpoint = false, useGoogleAuth = false } = options;
    let tokenProvider;

    if (useGoogleAuth) {
      tokenProvider = () => this.getIdToken(url);
    }
    
    const sanitizedUrl = url.replace(/\/$/, '');
    let sandbox;
    const connection = new Connection(
      `${sanitizedUrl}/create`,
      (code, reason) => sandbox.shouldReconnect(code, reason),
      () => sandbox.getReconnectInfo(),
      wsOptions,
      enableDebug,
      debugLabel,
      tokenProvider,
    );
    sandbox = new Sandbox(connection, enableDebug, debugLabel, enableAutoReconnect, useGoogleAuth);
    sandbox._url = url;
    sandbox._wsOptions = wsOptions;

    connection.once('open', () => {
      connection.send(JSON.stringify({
        idle_timeout: idleTimeout,
        enable_checkpoint: enableSandboxCheckpoint,
        enable_sandbox_handoff: enableSandboxHandoff,
        filesystem_snapshot_name: filesystemSnapshotName,
        enable_idle_timeout_auto_checkpoint: enableIdleTimeoutAutoCheckpoint,
      }));
    });
    
    return new Promise((resolve, reject) => {
      sandbox.eventEmitter.once('created', (createdSandbox) => {
        resolve(createdSandbox);
      });
      
      sandbox.eventEmitter.once('failed', (err) => {
        // On failure, ensure the socket is completely destroyed.
        connection.close();
        reject(err);
      });
    });
  }

  /**
   * @param {string} url
   * @param {string} sandboxId
   * @param {string} sandboxToken
   * @param {{ enableDebug?: boolean, debugLabel?: string, wsOptions?: import('ws').ClientOptions, enableAutoReconnect?: boolean, useGoogleAuth?: boolean }} [options]
   * @returns {Promise<Sandbox>}
   */
  static async attach(url, sandboxId, sandboxToken, options = {}) {
    const { enableDebug = false, debugLabel = '', wsOptions, enableAutoReconnect = false, useGoogleAuth = false } = options;
    let tokenProvider;

    if (useGoogleAuth) {
      tokenProvider = () => this.getIdToken(url);
    }
    
    const sanitizedUrl = url.replace(/\/$/, '');
    let sandbox;
    const connection = new Connection(
      `${sanitizedUrl}/attach/${sandboxId}?sandbox_token=${sandboxToken}`,
      (code, reason) => sandbox.shouldReconnect(code, reason),
      () => sandbox.getReconnectInfo(),
      wsOptions,
      enableDebug,
      debugLabel,
      tokenProvider,
    );
    sandbox = new Sandbox(connection, enableDebug, debugLabel, enableAutoReconnect, useGoogleAuth);
    sandbox._url = url;
    sandbox._wsOptions = wsOptions;
    sandbox._sandboxId = sandboxId;
    sandbox._sandboxToken = sandboxToken;

    return new Promise((resolve, reject) => {
      sandbox.eventEmitter.once('created', (createdSandbox) => {
        resolve(createdSandbox);
      });
      
      sandbox.eventEmitter.once('failed', (err) => {
        // On failure, ensure the socket is completely destroyed.
        connection.close();
        reject(err);
      });
    });
  }

  /**
   * @returns {Promise<void>}
   */
  checkpoint() {
    if (this.state !== 'running') {
      return Promise.reject(new Error(`Sandbox is not in a running state. Current state: ${this.state}`));
    }
    this._isCheckpointIntentionally = true;
    this.connection.send(JSON.stringify({ action: 'checkpoint' }));

    return new Promise((resolve, reject) => {
      this.eventEmitter.once('checkpointed', () => {
        resolve();
      });
      this.eventEmitter.once('checkpoint_error', (err) => {
        reject(err);
      });
    });
  }

  /**
   * @param {string} name
   * @returns {Promise<void>}
   */
  snapshotFilesystem(name) {
    if (this.state !== 'running') {
      return Promise.reject(new Error(`Sandbox is not in a running state. Current state: ${this.state}`));
    }

    this.connection.send(JSON.stringify({ action: 'snapshot_filesystem', name }));

    return new Promise((resolve, reject) => {
      this.eventEmitter.once('filesystem_snapshot_created', () => {
        resolve();
      });
      this.eventEmitter.once('filesystem_snapshot_error', (err) => {
        reject(err);
      });
    });
  }

  flushStdinBuffer() {
    this.logDebugMessage(`Flushing stdin buffer (${this.stdinBuffer.length} messages).`);
    for (const data of this.stdinBuffer) {
      this.connection.send(data);
    }
    this.stdinBuffer = [];
  }

  /**
   * @param {string} data
   */
  sendMessage(data) {
    if (this.state === 'reconnecting') {
      try {
        const message = JSON.parse(data);
        if (message.event === 'stdin') {
          this.logDebugMessage('Buffering stdin message while reconnecting:', message.data);
          this.stdinBuffer.push(data);
          return;
        }
      } catch (e) {
        // Not a JSON message, or doesn't have an event property.
        // Let it pass through. This shouldn't happen for stdin.
      }
    }
    this.connection.send(data);
  }

  /**
   * @param {string} language
   * @param {string} code
   * @returns {Promise<SandboxProcess>}
   */
  async exec(language, code) {
    if (this.activeProcess) {
      throw new Error('Another process is already running in this sandbox.');
    }
    if (this.state !== 'running') {
      throw new Error(`Sandbox is not in a running state. Current state: ${this.state}`);
    }

    const process = new SandboxProcess(
      this.sendMessage.bind(this),
    );
    this.activeProcess = process;
    
    process.eventEmitter.once('done', () => {
      this.activeProcess = null;
    });

    await process.exec(language, code);
    return process;
  }

  /**
   * @returns {Promise<void>}
   */
  kill() {
    if (this.state === 'closed' || this.state === 'failed' || this.state === 'checkpointed') {
      this.connection.close();
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        this.logDebugMessage('Kill timeout reached. Forcing connection close.');
        this.connection.close();
        resolve();
      }, 5000); // 5-second timeout

      this.eventEmitter.once('killed', () => {
        this.logDebugMessage('Sandbox killed successfully.');
        clearTimeout(timeout);
        this.connection.close();
        resolve();
      });

      this._isKillIntentionally = true;
      this.logDebugMessage('Sending kill command to sandbox...');
      this.connection.send(JSON.stringify({
        action: 'kill_sandbox',
      }));
    });
  }
}
