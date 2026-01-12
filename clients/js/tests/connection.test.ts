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

import { Connection, ShouldReconnectCallback } from '../src/connection';
import WebSocket from 'ws';

// Mock the WebSocket class
jest.mock('ws');

const MockWebSocket = WebSocket as jest.MockedClass<typeof WebSocket>;

describe('Connection', () => {
  const MOCK_URL = 'ws://localhost:8080';
  let mockWsInstance: jest.Mocked<WebSocket>;
  let shouldReconnectCallback: jest.Mock<boolean, [number, Buffer]>;
  let getReconnectInfoCallback: jest.Mock<any, []>;

  beforeEach(() => {
    // Create a fresh mock for each test to avoid state leakage
    mockWsInstance = {
      on: jest.fn(),
      send: jest.fn(),
      close: jest.fn(),
      readyState: WebSocket.OPEN,
    } as unknown as jest.Mocked<WebSocket>;

    // Mock the constructor to return our controlled instance
    MockWebSocket.mockClear();
    MockWebSocket.mockImplementation(() => mockWsInstance);

    shouldReconnectCallback = jest.fn();
    getReconnectInfoCallback = jest.fn().mockReturnValue({ url: MOCK_URL });
  });

  const waitForConnection = () => new Promise(resolve => setTimeout(resolve, 0));

      it('should establish a connection and emit "open"', async () => {
        const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
        await waitForConnection();
    
        const openCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'open')[1];
        
        const openListener = jest.fn();
        connection.on('open', openListener);
    
        openCallback(); // Simulate the 'open' event from the ws instance
    
        expect(MockWebSocket).toHaveBeenCalledWith(MOCK_URL, undefined);
        expect(openListener).toHaveBeenCalled();
      });
  
      it('should not pass empty headers options to WebSocket if not needed', async () => {
        new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
        await waitForConnection();
        
        // Verify the second argument to WebSocket constructor was undefined
        expect(MockWebSocket).toHaveBeenCalledWith(MOCK_URL, undefined);
      });
  
      it('should pass provided options to WebSocket', async () => {
        const options = { handshakeTimeout: 12345 };
        new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback, options);
        await waitForConnection();
        
        expect(MockWebSocket).toHaveBeenCalledWith(MOCK_URL, expect.objectContaining(options));
      });
  it('should emit "message" when a message is received', async () => {
    const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
    await waitForConnection();

    const messageCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'message')[1];

    const messageListener = jest.fn();
    connection.on('message', messageListener);

    const testData = 'hello world';
    messageCallback(testData);

    expect(messageListener).toHaveBeenCalledWith(testData);
  });

  it('should emit "error" when an error occurs', async () => {
    const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
    await waitForConnection();

    const errorCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'error')[1];

    const errorListener = jest.fn();
    connection.on('error', errorListener);

    const testError = new Error('Something went wrong');
    errorCallback(testError);

    expect(errorListener).toHaveBeenCalledWith(testError);
  });

  describe('closing and reconnection', () => {
    it('should close the connection and emit "close" when close() is called', async () => {
      const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
      await waitForConnection();

      const closeCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'close')[1];
      
      const closeListener = jest.fn();
      connection.on('close', closeListener);

      connection.close(1000, 'Normal closure');
      closeCallback(1000, Buffer.from('Normal closure'));

      expect(mockWsInstance.close).toHaveBeenCalledWith(1000, 'Normal closure');
      expect(closeListener).toHaveBeenCalledWith(1000, Buffer.from('Normal closure'));
      expect(shouldReconnectCallback).not.toHaveBeenCalled();
    });

    it('should attempt to reconnect if shouldReconnect returns true', async () => {
      shouldReconnectCallback.mockReturnValue(true);
      const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
      await waitForConnection();

      const closeCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'close')[1];
      const openCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'open')[1];

      const reopenListener = jest.fn();
      connection.on('reopen', reopenListener);

      // Simulate an unexpected close
      await closeCallback(1006, Buffer.from('Abnormal closure'));
      openCallback();

      expect(shouldReconnectCallback).toHaveBeenCalledWith(1006, Buffer.from('Abnormal closure'));
      expect(reopenListener).toHaveBeenCalled();
      // It should have been called once for the initial connection, and once for the reconnect.
      expect(MockWebSocket).toHaveBeenCalledTimes(2);
    });

    it('should not reconnect and emit "close" if shouldReconnect returns false', async () => {
      shouldReconnectCallback.mockReturnValue(false);
      const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
      await waitForConnection();

      const closeCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'close')[1];

      const closeListener = jest.fn();
      const reopenListener = jest.fn();
      connection.on('close', closeListener);
      connection.on('reopen', reopenListener);

      // Simulate an unexpected close
      await closeCallback(1001, Buffer.from('Going away'));

      expect(shouldReconnectCallback).toHaveBeenCalledWith(1001, Buffer.from('Going away'));
      expect(reopenListener).not.toHaveBeenCalled();
      expect(closeListener).toHaveBeenCalledWith(1001, Buffer.from('Going away'));
      // Should only be called for the initial connection
      expect(MockWebSocket).toHaveBeenCalledTimes(1);
    });
  });

  describe('sending data', () => {
    it('should call the underlying ws.send() method', async () => {
      const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
      await waitForConnection();
      const data = 'test data';
      connection.send(data);
      expect(mockWsInstance.send).toHaveBeenCalledWith(data);
    });

    it('should emit an error if send() is called when the socket is not open', async () => {
      const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
      await waitForConnection();

      // Simulate the socket not being open
      Object.defineProperty(mockWsInstance, 'readyState', { value: WebSocket.CLOSED });

      const errorListener = jest.fn();
      connection.on('error', errorListener);

      connection.send('some data');

      expect(mockWsInstance.send).not.toHaveBeenCalled();
      expect(errorListener).toHaveBeenCalledWith(expect.any(Error));
      expect(errorListener.mock.calls[0][0].message).toBe('WebSocket is not open. Cannot send data.');
    });
  });

  describe('session affinity', () => {
    it('should capture the set-cookie header on upgrade and use it for reconnect', async () => {
      new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);
      await waitForConnection();

      const upgradeCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'upgrade')[1];

      const mockResponse = {
        headers: {
          'set-cookie': ['GAESA=test-cookie;'],
        },
      };

      upgradeCallback(mockResponse);

      // To verify the cookie is stored, we'll check if it's sent on reconnect
      shouldReconnectCallback.mockReturnValue(true);
      const closeCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'close')[1];
      await closeCallback(1006, Buffer.from('Abnormal closure'));

      expect(MockWebSocket).toHaveBeenCalledTimes(2);
      expect(MockWebSocket).toHaveBeenLastCalledWith(MOCK_URL, {
        headers: {
          Cookie: 'GAESA=test-cookie;',
        },
      });
    });
  });

  describe('authentication', () => {
    it('should use the token from tokenProvider', async () => {
      const tokenProvider = jest.fn().mockResolvedValue('test-token');
      new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback, undefined, false, '', tokenProvider);
      
      await waitForConnection();

      expect(tokenProvider).toHaveBeenCalled();
      expect(MockWebSocket).toHaveBeenCalledWith(MOCK_URL, {
        headers: {
          Authorization: 'Bearer test-token',
        },
      });
    });

    it('should fail fast if tokenProvider fails', async () => {
      const tokenProvider = jest.fn().mockRejectedValue(new Error('Auth failed'));
      const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback, undefined, false, '', tokenProvider);

      const errorListener = jest.fn();
      connection.on('error', errorListener);

      await waitForConnection();

      expect(tokenProvider).toHaveBeenCalled();
      expect(errorListener).toHaveBeenCalledWith(expect.objectContaining({ message: 'Auth failed' }));
      // Should NOT have attempted to connect
      expect(MockWebSocket).not.toHaveBeenCalled();
    });

    it('should send both Cookie and Authorization header if both are present', async () => {
      // 1. Setup token provider
      const tokenProvider = jest.fn().mockResolvedValue('test-token');
      
      // 2. Setup initial connection to capture a cookie
      const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback, undefined, false, '', tokenProvider);
      await waitForConnection();

      // Verify first connection used token
      expect(MockWebSocket).toHaveBeenCalledWith(MOCK_URL, {
        headers: { Authorization: 'Bearer test-token' },
      });

      // 3. Simulate receiving a cookie
      const upgradeCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'upgrade')[1];
      const mockResponse = {
        headers: { 'set-cookie': ['GAESA=test-cookie;'] },
      };
      upgradeCallback(mockResponse);

      // 4. Trigger reconnect
      shouldReconnectCallback.mockReturnValue(true);
      const closeCallback = (mockWsInstance.on as jest.Mock).mock.calls.find(call => call[0] === 'close')[1];
      
      // Clear mock to check the NEXT call
      MockWebSocket.mockClear();
      
      await closeCallback(1006, Buffer.from('Abnormal closure'));
      
      // 5. Verify RECONNECT used BOTH cookie and token
      expect(MockWebSocket).toHaveBeenCalledWith(MOCK_URL, {
        headers: {
          Authorization: 'Bearer test-token',
          Cookie: 'GAESA=test-cookie;',
        },
      });
    });
  });
});
