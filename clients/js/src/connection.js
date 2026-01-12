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

/**
 * A callback function that determines whether the connection should attempt to reconnect.
 * @typedef {(code: number, reason: Buffer) => boolean} ShouldReconnectCallback
 */

/**
 * @typedef {Object} ReconnectInfo
 * @property {string} url
 * @property {WebSocket.ClientOptions} [wsOptions]
 */

/**
 * @typedef {() => ReconnectInfo} GetReconnectInfoCallback
 */

/**
 * A WebSocket connection wrapper that provides automatic reconnection capabilities.
 * It emits standard WebSocket events (`open`, `message`, `close`, `error`) and an additional `reopen` event
 * when it's about to attempt a reconnection.
 */
export class Connection extends EventEmitter {
  /**
   * Creates an instance of the Connection class.
   * @param {string} url The URL to connect to.
   * @param {ShouldReconnectCallback} shouldReconnect A callback that is invoked when the connection closes to determine if a reconnect should be attempted.
   * @param {GetReconnectInfoCallback} getReconnectInfo A callback that returns the URL and options for reconnection.
   * @param {WebSocket.ClientOptions} [wsOptions] Optional client options for the underlying `ws` WebSocket instance.
   * @param {boolean} [debug]
   * @param {string} [debugLabel]
   * @param {() => Promise<string>} [tokenProvider]
   */
  constructor(
    url,
    shouldReconnect,
    getReconnectInfo,
    wsOptions,
    debug = false,
    debugLabel = '',
    tokenProvider,
  ) {
    super();
    this.url = url;
    this.shouldReconnect = shouldReconnect;
    this.getReconnectInfo = getReconnectInfo;
    this.wsOptions = wsOptions;
    this._debugEnabled = debug;
    this._debugLabel = debugLabel;
    this.tokenProvider = tokenProvider;

    /** @type {WebSocket | null} */
    this.ws = null;
    this.isReconnecting = false;
    /** @type {string | null} */
    this.cookie = null;
    
    /**
     * A flag to indicate if the connection was closed intentionally by the user calling `close()`.
     * If `true`, no reconnection attempts will be made.
     */
    this.isClosedIntentionally = false;
    
    this.connect();
  }

  /**
   * Establishes the WebSocket connection and sets up event listeners.
   * This method is called initially and for every reconnection attempt.
   */
  async connect() {
    /** @type {Record<string, string> | undefined} */
    let headers = this.wsOptions?.headers;
    
    let token;
    if (this.tokenProvider) {
      try {
        token = await this.tokenProvider();
      } catch (e) {
        this.emit('error', e);
        return;
      }
    }

    if (this.cookie || token) {
      headers = { ...headers };
      if (this.cookie) {
        if (this._debugEnabled) {
          console.log(`[${this._debugLabel}] [DEBUG] Using cookie for connection:`, this.cookie);
        }
        headers['Cookie'] = this.cookie;
      }
      if (token) {
        if (this._debugEnabled) {
          console.log(`[${this._debugLabel}] [DEBUG] Using authentication token.`);
        }
        headers['Authorization'] = `Bearer ${token}`;
      }
    }

    if (headers) {
      this.wsOptions = {
        ...this.wsOptions,
        headers,
      };
    }

    this.ws = new WebSocket(this.url, this.wsOptions);

    this.ws.on('upgrade', (response) => {
      const cookie = response.headers['set-cookie'];
      if (cookie) {
        if (this._debugEnabled) {
          console.log(`[${this._debugLabel}] [DEBUG] Received cookie:`, cookie[0]);
        }
        this.cookie = cookie[0];
      }
    });

    this.ws.on('open', () => {
      if (this.isReconnecting) {
        this.isReconnecting = false;
        this.emit('reopen');
      } else {
        this.emit('open');
      }
    });

    /**
     * Emitted when a message is received from the server.
     * @event message
     * @param {WebSocket.Data} data The received data.
     */
    this.ws.on('message', (data) => {
      this.emit('message', data);
    });

    /**
     * Handles the closing of the connection.
     * If the close was not intentional, it consults the `shouldReconnect` callback.
     */
    this.ws.on('close', (code, reason) => {
      // If the connection was closed intentionally, do not reconnect.
      if (this.isClosedIntentionally) {
        /**
         * Emitted when the connection has been closed.
         * @event close
         * @param {number} code The closing status code.
         * @param {Buffer} reason The reason for closing.
         */
        this.emit('close', code, reason);
        return;
      }

      // Ask the user if we should reconnect.
      if (this.shouldReconnect(code, reason)) {
        this.isReconnecting = true;
        const reconnectInfo = this.getReconnectInfo();
        this.url = reconnectInfo.url;
        this.wsOptions = reconnectInfo.wsOptions || this.wsOptions;
        this.connect();
      } else {
        this.emit('close', code, reason);
      }
    });

    /**
     * Emitted when an error occurs on the connection.
     * @event error
     * @param {Error} err The error object.
     */
    this.ws.on('error', (err) => {
      this.emit('error', err);
    });
  }

  /**
   * Sends data over the WebSocket connection.
   * @param {string | Buffer | ArrayBuffer | Buffer[]} data The data to send.
   */
  send(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(data);
    } else {
      this.emit('error', new Error('WebSocket is not open. Cannot send data.'));
    }
  }

  /**
   * Closes the WebSocket connection intentionally.
   * Once this is called, no reconnection attempts will be made.
   * @param {number} [code] The status code for closing.
   * @param {string} [reason] The reason for closing.
   */
  close(code, reason) {
    this.isClosedIntentionally = true;
    if (this.ws) {
      this.ws.close(code, reason);
    }
  }
}
