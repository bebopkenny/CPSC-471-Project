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

### Quitting

To quit, type `disconnect` on each client side. Then, end the server process by pressing `ctrl + C`.

---

# Http proxy server part 2 

HTTP proxy server deployed on an AWS EC2 instance. It forwards HTTP requests from your local machine through the EC2 server to the destination.

## Running the proxy

The proxy runs on an AWS EC2 instance. The `my-key.pem` file is required to connect to the server. If you get a permissions error on the key, run `chmod 400 my-key.pem`.

Start the EC2 instance from the EC2 console. Click on `Instances`, right click the instance, and click `Start Instance`. Wait for the status to show `Running` and for status checks to pass.

Note the Public IPv4 address from the instance details. It may change each time the instance is restarted.

SSH into the server, pull the latest code, and start the proxy:

``` bash
ssh -i "my-key.pem" ubuntu@<PUBLIC_IP>
cd ~/CPSC-471-Project
git pull origin main
nohup python3 phase2/proxy.py 8080 > proxy.log 2>&1 &
exit
```

Use the proxy from your local machine:

``` bash
curl -x http://<PUBLIC_IP>:8080 http://example.com
```

To check the proxy logs:

``` bash
ssh -i "my-key.pem" ubuntu@<PUBLIC_IP> "cat ~/CPSC-471-Project/proxy.log"
```

### Stopping

When done, stop the EC2 instance from the EC2 console so we don't run out of free credits. Right click the instance and click `Stop Instance`.
