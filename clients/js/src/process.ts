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

import { EventEmitter } from 'events';
import { Readable } from 'stream';
import { MessageKey, EventType, SandboxEvent, WebSocketMessage } from './types';

type SendMessageCallback = (data: string) => void;

class SandboxStream extends Readable {
  // This is a push-based stream. Data is pushed into it from an external
  // source (the WebSocket message handler) via the `push()` method. The
  // _read() method is a no-op because the stream itself doesn't actively
  // fetch data; it just waits for data to be pushed.
  _read() {}

  /**
   * Reads the entire stream until EOF and returns it as a single string.
   */
  public async readAll(): Promise<string> {
    const chunks: Buffer[] = [];
    for await (const chunk of this) {
      chunks.push(Buffer.from(chunk));
    }
    return Buffer.concat(chunks).toString('utf-8');
  }
}

export class SandboxProcess {
  private send: SendMessageCallback;
  private eventEmitter = new EventEmitter();
  private _startError: Error | null = null;
  private _isDone: boolean = false;
  private _isKillIntentionally: boolean = false;
  private _isKilling = false;

  public readonly stdout: SandboxStream;
  public readonly stderr: SandboxStream;

  constructor(send: SendMessageCallback) {
    this.send = send;
    this.stdout = new SandboxStream();
    this.stderr = new SandboxStream();
  }

  public handleMessage(message: WebSocketMessage) {
    if (this._isDone) {
      return; // Don't process any more messages after completion.
    }

    switch (message.event) {
      case EventType.STDOUT:
        this.stdout.push(message.data);
        break;
      
      case EventType.STDERR:
        this.stderr.push(message.data);
        break;

      case EventType.STATUS_UPDATE:
        if (message.status === SandboxEvent.SANDBOX_EXECUTION_RUNNING) {
          this.eventEmitter.emit('started');
        } else if (message.status === SandboxEvent.SANDBOX_EXECUTION_ERROR) {
          this._startError = new Error(message.message || 'Sandbox execution failed');
          this.eventEmitter.emit('started');
          this.close();
        } else if (message.status === SandboxEvent.SANDBOX_EXECUTION_UNSUPPORTED_LANGUAGE_ERROR) {
          this._startError = new Error(message.message || 'Unsupported language');
          this.eventEmitter.emit('started');
          this.close();
        } else if (message.status === SandboxEvent.SANDBOX_EXECUTION_DONE) {
          if (this._isKilling) {
            this.eventEmitter.emit('killed');
          }
          this.close();
        } else if (
          message.status === SandboxEvent.SANDBOX_EXECUTION_FORCE_KILLED ||
          message.status === SandboxEvent.SANDBOX_EXECUTION_FORCE_KILL_ERROR
        ) {
          if (this._isKillIntentionally) {
            this._isKilling = true;
          } else {
            this.close();
          }
        }
        break;
    }
  }

  public async exec(language: string, code: string): Promise<void> {
    return new Promise((resolve, reject) => {
      this.eventEmitter.once('started', () => {
        if (this._startError) {
          reject(this._startError);
        } else {
          resolve();
        }
      });

      this.send(JSON.stringify({
        language,
        code,
      }));
    });
  }

  public async wait(): Promise<void> {
    if (this._isDone) {
      return Promise.resolve();
    }
    
    return new Promise((resolve) => {
      this.eventEmitter.once('done', () => {
        resolve();
      });
    });
  }

  public writeToStdin(data: string): void {
    if (this._isDone) {
      throw new Error("Process has already completed.");
    }
    this.send(JSON.stringify({
      event: 'stdin',
      data,
    }));
  }

  private cleanup() {
    this.stdout.push(null);
    this.stderr.push(null);
  }

  public close(): void {
    if (!this._isDone) {
      this._isDone = true;
      this.cleanup();
      this.eventEmitter.emit('done');
    }
  }

  public kill(): Promise<void> {
    if (this._isDone) {
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        this.close();
        resolve();
      }, 5000); // 5-second timeout

      this.eventEmitter.once('killed', () => {
        clearTimeout(timeout);
        this.close();
        resolve();
      });

      this._isKillIntentionally = true;
      this.send(JSON.stringify({
        action: 'kill_process',
      }));
    });
  }
}