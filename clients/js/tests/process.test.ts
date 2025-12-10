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

import { SandboxProcess } from '../src/process';
import { EventEmitter } from 'events';
import { MessageKey, EventType, SandboxEvent, WebSocketMessage } from '../src/types';

/**
 * A test helper to simulate the server side of the WebSocket connection.
 */
class MockServer {
  public readonly send: jest.Mock;
  private process: SandboxProcess | null = null;

  constructor() {
    this.send = jest.fn();
  }

  setProcess(process: SandboxProcess) {
    this.process = process;
  }

  public sendMessage(message: Partial<WebSocketMessage>) {
    if (!this.process) {
      throw new Error('No process connected to the mock server');
    }
    this.process.handleMessage(message as WebSocketMessage);
  }

  sendRunning() {
    this.sendMessage({
      [MessageKey.EVENT]: EventType.STATUS_UPDATE,
      [MessageKey.STATUS]: SandboxEvent.SANDBOX_EXECUTION_RUNNING,
    });
  }

  sendStdout(data: string) {
    this.sendMessage({ [MessageKey.EVENT]: EventType.STDOUT, [MessageKey.DATA]: data });
  }

  sendStderr(data: string) {
    this.sendMessage({ [MessageKey.EVENT]: EventType.STDERR, [MessageKey.DATA]: data });
  }

  sendDone() {
    this.sendMessage({
      [MessageKey.EVENT]: EventType.STATUS_UPDATE,
      [MessageKey.STATUS]: SandboxEvent.SANDBOX_EXECUTION_DONE,
    });
  }

  sendError(errorMessage: string) {
    this.sendMessage({
      [MessageKey.EVENT]: EventType.STATUS_UPDATE,
      [MessageKey.STATUS]: SandboxEvent.SANDBOX_EXECUTION_ERROR,
      [MessageKey.MESSAGE]: errorMessage,
    });
  }

  sendUnsupportedLanguageError(errorMessage: string) {
    this.sendMessage({
      [MessageKey.EVENT]: EventType.STATUS_UPDATE,
      [MessageKey.STATUS]: SandboxEvent.SANDBOX_EXECUTION_UNSUPPORTED_LANGUAGE_ERROR,
      [MessageKey.MESSAGE]: errorMessage,
    });
  }

  sendForcedKill() {
    this.sendMessage({
      [MessageKey.EVENT]: EventType.STATUS_UPDATE,
      [MessageKey.STATUS]: SandboxEvent.SANDBOX_EXECUTION_FORCE_KILLED,
    });
  }
}


describe('SandboxProcess', () => {
  let server: MockServer;

  beforeEach(() => {
    server = new MockServer();
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('sends the correct execution request to the server', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);

    // Act
    const execPromise = process.exec('python', 'print("hello")');
    server.sendRunning();
    await execPromise;

    // Assert
    expect(server.send).toHaveBeenCalledWith(JSON.stringify({
      language: 'python',
      code: 'print("hello")',
    }));
  });

  it('resolves the wait promise when called before the done message', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    // Act
    const waitPromise = process.wait();
    server.sendDone();

    // Assert
    await expect(waitPromise).resolves.toBeUndefined();
  });

  it('resolves the wait promise when called after the done message', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    // Act
    server.sendDone();
    const waitPromise = process.wait(); // Called after 'done'

    // Assert
    await expect(waitPromise).resolves.toBeUndefined();
  });

  it('streams stdout and stderr chunks correctly using full read', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);

    // Act
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    const allPromise = Promise.all([
      process.stdout.readAll(),
      process.stderr.readAll(),
      process.wait(),
    ]);

    server.sendStdout('Hello, ');
    server.sendStderr('Error, ');
    server.sendStdout('World!');
    server.sendStderr('Test!');
    server.sendDone();
    
    const [stdout, stderr] = await allPromise;
    
    // Assert
    expect(stdout).toBe('Hello, World!');
    expect(stderr).toBe('Error, Test!');
  });

  it('streams stdout and stderr chunks correctly using iterative read', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);
    const stdoutChunks: string[] = [];
    const stderrChunks: string[] = [];

    let resolveStdout1: () => void;
    const stdoutPromise1 = new Promise<void>(resolve => { resolveStdout1 = resolve; });
    
    let resolveStdout2: () => void;
    const stdoutPromise2 = new Promise<void>(resolve => { resolveStdout2 = resolve; });

    let resolveStderr1: () => void;
    const stderrPromise1 = new Promise<void>(resolve => { resolveStderr1 = resolve; });

    // Act
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    const readStreams = async () => {
      const stdoutIterator = process.stdout[Symbol.asyncIterator]();
      const stderrIterator = process.stderr[Symbol.asyncIterator]();

      let chunk = await stdoutIterator.next();
      stdoutChunks.push(chunk.value.toString());
      resolveStdout1();

      chunk = await stderrIterator.next();
      stderrChunks.push(chunk.value.toString());
      resolveStderr1();

      chunk = await stdoutIterator.next();
      stdoutChunks.push(chunk.value.toString());
      resolveStdout2();
    };
    
    const allPromise = Promise.all([
      readStreams(),
      process.wait(),
    ]);
    
    server.sendStdout('chunk1');
    await stdoutPromise1;
    
    server.sendStderr('error1');
    await stderrPromise1;

    server.sendStdout('chunk2');
    await stdoutPromise2;

    server.sendDone();

    await allPromise;
    
    // Assert
    expect(stdoutChunks).toEqual(['chunk1', 'chunk2']);
    expect(stderrChunks).toEqual(['error1']);
  });

  it('rejects the exec promise when the server sends an execution error', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);

    // Act
    const execPromise = process.exec('python', 'bad code');
    server.sendError('Syntax error');

    // Assert
    await expect(execPromise).rejects.toThrow('Syntax error');
  });

  it('rejects the exec promise on unsupported language error', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);

    // Act
    const execPromise = process.exec('javascript', 'console.log("hello")');
    server.sendUnsupportedLanguageError('Unsupported language');

    // Assert
    await expect(execPromise).rejects.toThrow('Unsupported language');
  });

  it('does not hang if stdout is fully consumed before wait() is called', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);

    // Act
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    const streamPromise = process.stdout.readAll();

    server.sendStdout('some data');
    server.sendDone(); // This is the key: the stream should end here.

    // Assert
    await expect(streamPromise).resolves.toBe('some data');
    await expect(process.wait()).resolves.toBeUndefined();
  });

  it('sends stdin to the server', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);

    // Act
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    process.writeToStdin('hello');

    // Assert
    expect(server.send).toHaveBeenCalledWith(JSON.stringify({
      event: 'stdin',
      data: 'hello',
    }));
  });

  it('sends a kill action to the server', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);

    // Act
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    process.kill();

    // Assert
    expect(server.send).toHaveBeenCalledWith(JSON.stringify({
      action: 'kill_process',
    }));
  });

  it('waits for both FORCE_KILLED and DONE events on kill', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    // Act
    const killPromise = process.kill();

    // Simulate the server sending the two events in order
    server.sendForcedKill();
    server.sendDone();

    // Assert
    await expect(killPromise).resolves.toBeUndefined();
    await expect(process.wait()).resolves.toBeUndefined();
  });

  it('resolves kill and wait promises on timeout', async () => {
    // Arrange
    const process = new SandboxProcess(server.send);
    server.setProcess(process);
    const execPromise = process.exec('bash', 'test');
    server.sendRunning();
    await execPromise;

    // Act
    const killPromise = process.kill();
    const waitPromise = process.wait();
    jest.runAllTimers();

    // Assert
    await expect(killPromise).resolves.toBeUndefined();
    await expect(waitPromise).resolves.toBeUndefined();
  });
});