# Network Programming Project - Phase 1

Client-server socket application for text messages that binds to a user-specified or default port.

## Team Members

- Kenny Garcia
- Mason Jennings
- Kyler Geesink
- Daniel Boynton

## Running the program

This program is run with `python3`.

`server.py` acts as the server side while `client.py` acts as an edge device. You can run them both in separate terminal windows.

A specific port number can be specified as a command line argument between 1 and 9999. If blank or out-of-range, both client and server default to port 5050.

See below for how these functions are called.

``` bash
python3 server.py <PORT NUMBER>
```

``` bash
python3 client.py <PORT NUMBER>
```
