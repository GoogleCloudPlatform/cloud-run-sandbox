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
 * @param code The WebSocket closing status code.
 * @param reason A Buffer containing the reason for closing.
 * @returns `true` if a reconnection should be attempted, `false` otherwise.
 */
export type ShouldReconnectCallback = (code: number, reason: Buffer) => boolean;

export type ReconnectInfo = {
  url: string;
  wsOptions?: WebSocket.ClientOptions;
};

export type GetReconnectInfoCallback = () => ReconnectInfo;

/**
 * A WebSocket connection wrapper that provides automatic reconnection capabilities.
 * It emits standard WebSocket events (`open`, `message`, `close`, `error`) and an additional `reopen` event
 * when it's about to attempt a reconnection.
 */
export class Connection extends EventEmitter {
  private ws: WebSocket | null = null;
  private url: string;
  private wsOptions?: WebSocket.ClientOptions;
  private shouldReconnect: ShouldReconnectCallback;
  private getReconnectInfo: GetReconnectInfoCallback;
  private isReconnecting: boolean = false;
  private cookie: string | null = null;
  private _debugEnabled: boolean = false;
  private _debugLabel: string = '';
  private tokenProvider?: () => Promise<string>;

  /**
   * A flag to indicate if the connection was closed intentionally by the user calling `close()`.
   * If `true`, no reconnection attempts will be made.
   */
  private isClosedIntentionally: boolean = false;

  /**
   * Creates an instance of the Connection class.
   * @param url The URL to connect to.
   * @param shouldReconnect A callback that is invoked when the connection closes to determine if a reconnect should be attempted.
   * @param getReconnectInfo A callback that returns the URL and options for reconnection.
   * @param wsOptions Optional client options for the underlying `ws` WebSocket instance.
   */
  constructor(
    url: string,
    shouldReconnect: ShouldReconnectCallback,
    getReconnectInfo: GetReconnectInfoCallback,
    wsOptions?: WebSocket.ClientOptions,
    debug: boolean = false,
    debugLabel: string = '',
    tokenProvider?: () => Promise<string>,
  ) {
    super();
    this.url = url;
    this.shouldReconnect = shouldReconnect;
    this.getReconnectInfo = getReconnectInfo;
    this.wsOptions = wsOptions;
    this._debugEnabled = debug;
    this._debugLabel = debugLabel;
    this.tokenProvider = tokenProvider;
    this.connect();
  }

  /**
   * Establishes the WebSocket connection and sets up event listeners.
   * This method is called initially and for every reconnection attempt.
   */
  private async connect() {
    let headers: Record<string, string> | undefined = this.wsOptions?.headers as any;
    
    let token: string | undefined;
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
   * @param data The data to send.
   */
  public send(data: string | Buffer | ArrayBuffer | Buffer[]) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(data);
    } else {
      this.emit('error', new Error('WebSocket is not open. Cannot send data.'));
    }
  }

  /**
   * Closes the WebSocket connection intentionally.
   * Once this is called, no reconnection attempts will be made.
   * @param code The status code for closing.
   * @param reason The reason for closing.
   */
  public close(code?: number, reason?: string) {
    this.isClosedIntentionally = true;
    if (this.ws) {
      this.ws.close(code, reason);
    }
  }
}
