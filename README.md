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

---

# Reverse proxy and data server part 2

Inbound (reverse) proxy that protects files on a private data server. The data server binds to the loopback interface only, so the only way to reach its files from outside the EC2 instance is through the reverse proxy. The proxy applies access control before forwarding any request to the data server.

Files under `phase2/data/public/` are open to anyone. Files under `phase2/data/private/` require an `X-Auth-Token` header that matches the secret the operator passed at startup through the `PROXY_AUTH_TOKEN` environment variable, so the token is never committed to source.

This runs alongside the forward proxy on the same EC2 instance. The forward proxy uses port `8080`, the reverse proxy uses port `8081`, and the data server stays internal on port `9000`.

## Running the reverse proxy

The data server must be started first. The reverse proxy connects to it on every request and will return `502 Bad Gateway` if the data server is not running.

SSH into the server, pull the latest code, and start both processes:

``` bash
ssh -i "my-key.pem" ubuntu@<PUBLIC_IP>
cd ~/CPSC-471-Project
git pull origin main
nohup python3 phase2/data_server.py 9000 > data_server.log 2>&1 &
PROXY_AUTH_TOKEN=<YOUR_SECRET> nohup python3 phase2/inbound_proxy.py 8081 > inbound_proxy.log 2>&1 &
exit
```

Replace `<YOUR_SECRET>` with any token value you want clients to present. The reverse proxy will refuse to start if `PROXY_AUTH_TOKEN` is not set.

Use the reverse proxy from your local machine. Public files need no authentication:

``` bash
curl http://<PUBLIC_IP>:8081/public/welcome.txt
```

Private files require the same token the proxy was started with:

``` bash
curl -H "X-Auth-Token: <YOUR_SECRET>" http://<PUBLIC_IP>:8081/private/notes.txt
```

To check the reverse proxy and data server logs:

``` bash
ssh -i "my-key.pem" ubuntu@<PUBLIC_IP> "cat ~/CPSC-471-Project/inbound_proxy.log"
ssh -i "my-key.pem" ubuntu@<PUBLIC_IP> "cat ~/CPSC-471-Project/data_server.log"
```

### Stopping

When done, stop the EC2 instance from the EC2 console so we don't run out of free credits. Right click the instance and click `Stop Instance`.
