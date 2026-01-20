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

import { jest } from '@jest/globals';



const mockWebSocket = jest.fn();

mockWebSocket.OPEN = 1;

mockWebSocket.CLOSED = 3;



// Mock the WebSocket class



jest.unstable_mockModule('ws', () => ({



  __esModule: true,



  default: mockWebSocket,



}));







const { Connection } = await import('../src/connection.js');







describe('Connection', () => {





  const MOCK_URL = 'ws://localhost:8080';

  let mockWsInstance;

  let shouldReconnectCallback;

  let getReconnectInfoCallback;



  beforeEach(() => {

    // Create a fresh mock for each test to avoid state leakage

    mockWsInstance = {

      on: jest.fn(),

      send: jest.fn(),

      close: jest.fn(),

      readyState: 1, // WebSocket.OPEN

    };



    // Mock the constructor to return our controlled instance

    mockWebSocket.mockClear();

    mockWebSocket.mockImplementation(() => mockWsInstance);



    shouldReconnectCallback = jest.fn();

    getReconnectInfoCallback = jest.fn().mockReturnValue({ url: MOCK_URL });

  });



  const waitForConnection = () => new Promise(resolve => setTimeout(resolve, 0));



      it('should establish a connection and emit "open"', async () => {

        const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);

        await waitForConnection();

    

        const openCallback = (mockWsInstance.on).mock.calls.find(call => call[0] === 'open')[1];

        

        const openListener = jest.fn();

        connection.on('open', openListener);

    

        openCallback(); // Simulate the 'open' event from the ws instance

    

        expect(mockWebSocket).toHaveBeenCalledWith(MOCK_URL, undefined);

        expect(openListener).toHaveBeenCalled();

      });

  

      it('should not pass empty headers options to WebSocket if not needed', async () => {

        new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);

        await waitForConnection();

        

        // Verify the second argument to WebSocket constructor was undefined

        expect(mockWebSocket).toHaveBeenCalledWith(MOCK_URL, undefined);

      });

  

      it('should pass provided options to WebSocket', async () => {

        const options = { handshakeTimeout: 12345 };

        new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback, options);

        await waitForConnection();

        

        expect(mockWebSocket).toHaveBeenCalledWith(MOCK_URL, expect.objectContaining(options));

      });

  it('should emit "message" when a message is received', async () => {

    const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);

    await waitForConnection();



    const messageCallback = (mockWsInstance.on).mock.calls.find(call => call[0] === 'message')[1];



    const messageListener = jest.fn();

    connection.on('message', messageListener);



    const testData = 'hello world';

    messageCallback(testData);



    expect(messageListener).toHaveBeenCalledWith(testData);

  });



  it('should emit "error" when an error occurs', async () => {

    const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);

    await waitForConnection();



    const errorCallback = (mockWsInstance.on).mock.calls.find(call => call[0] === 'error')[1];



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



      const closeCallback = (mockWsInstance.on).mock.calls.find(call => call[0] === 'close')[1];

      

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



      const closeCallback = (mockWsInstance.on).mock.calls.find(call => call[0] === 'close')[1];

      const openCallback = (mockWsInstance.on).mock.calls.find(call => call[0] === 'open')[1];



      const reopenListener = jest.fn();

      connection.on('reopen', reopenListener);



      // Simulate an unexpected close

      await closeCallback(1006, Buffer.from('Abnormal closure'));

      openCallback();



      expect(shouldReconnectCallback).toHaveBeenCalledWith(1006, Buffer.from('Abnormal closure'));

      expect(reopenListener).toHaveBeenCalled();

      // It should have been called once for the initial connection, and once for the reconnect.

      expect(mockWebSocket).toHaveBeenCalledTimes(2);

    });



    it('should not reconnect and emit "close" if shouldReconnect returns false', async () => {

      shouldReconnectCallback.mockReturnValue(false);

      const connection = new Connection(MOCK_URL, shouldReconnectCallback, getReconnectInfoCallback);

      await waitForConnection();



      const closeCallback = (mockWsInstance.on).mock.calls.find(call => call[0] === 'close')[1];



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

      expect(mockWebSocket).toHaveBeenCalledTimes(1);

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

      mockWsInstance.readyState = 3; // WebSocket.CLOSED

      

      const errorListener = jest.fn();

      connection.on('error', errorListener);

      

      connection.send('data');

      

      expect(errorListener).toHaveBeenCalledWith(expect.any(Error));

    });

  });

});
