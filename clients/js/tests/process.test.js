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

import { SandboxProcess } from '../src/process.js';
import { EventType, SandboxEvent } from '../src/types.js';
import { jest } from '@jest/globals';

describe('SandboxProcess', () => {
  let sendMock;
  let process;

  beforeEach(() => {
    sendMock = jest.fn();
    process = new SandboxProcess(sendMock);
  });

  it('should send execution request when exec is called', async () => {
    const execPromise = process.exec('python', 'print("hello")');
    
    // Simulate the server confirming the process started
    process.handleMessage({
      event: EventType.STATUS_UPDATE,
      status: SandboxEvent.SANDBOX_EXECUTION_RUNNING,
    });

    await execPromise;

    expect(sendMock).toHaveBeenCalledWith(JSON.stringify({
      language: 'python',
      code: 'print("hello")',
    }));
  });

  it('should reject exec if start fails', async () => {
    const execPromise = process.exec('python', 'print("hello")');
    
    // Simulate the server reporting an error
    process.handleMessage({
      event: EventType.STATUS_UPDATE,
      status: SandboxEvent.SANDBOX_EXECUTION_ERROR,
      message: 'Failed to start',
    });

    await expect(execPromise).rejects.toThrow('Failed to start');
  });

  it('should push stdout data to the stream', async () => {
    const dataPromise = process.stdout.readAll();

    process.handleMessage({
      event: EventType.STDOUT,
      data: 'output line 1\n',
    });
    process.handleMessage({
      event: EventType.STDOUT,
      data: 'output line 2',
    });
    
    // End the stream
    process.close();

    const data = await dataPromise;
    expect(data).toBe('output line 1\noutput line 2');
  });

  it('should push stderr data to the stream', async () => {
    const dataPromise = process.stderr.readAll();

    process.handleMessage({
      event: EventType.STDERR,
      data: 'error line 1\n',
    });
    
    // End the stream
    process.close();

    const data = await dataPromise;
    expect(data).toBe('error line 1\n');
  });

  it('should resolve wait() when process is done', async () => {
    const waitPromise = process.wait();
    
    process.handleMessage({
      event: EventType.STATUS_UPDATE,
      status: SandboxEvent.SANDBOX_EXECUTION_DONE,
    });

    await waitPromise;
  });

  it('should send kill command when kill() is called', async () => {
    const killPromise = process.kill();
    
    expect(sendMock).toHaveBeenCalledWith(JSON.stringify({
      action: 'kill_process',
    }));

    // Simulate kill confirmation
    process.handleMessage({
      event: EventType.STATUS_UPDATE,
      status: SandboxEvent.SANDBOX_EXECUTION_FORCE_KILLED,
    });
    // Simulate process done after kill (required for kill() to resolve)
    process.handleMessage({
      event: EventType.STATUS_UPDATE,
      status: SandboxEvent.SANDBOX_EXECUTION_DONE,
    });

    await killPromise;
  });
  
  it('should write to stdin', () => {
    process.writeToStdin('input data');
    expect(sendMock).toHaveBeenCalledWith(JSON.stringify({
      event: 'stdin',
      data: 'input data',
    }));
  });

  it('should throw error when writing to stdin of closed process', () => {
    process.close();
    expect(() => process.writeToStdin('data')).toThrow("Process has already completed.");
  });
});
