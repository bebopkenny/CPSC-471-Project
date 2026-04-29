import sys
import time
import socket
import threading
from urllib.parse import urlsplit

DEFAULT_PORT = 8080
LISTEN_HOST = "0.0.0.0"
RECV_CHUNK = 4096
ORIGIN_TIMEOUT = 10
CACHE_TTL = 60

# (host, port, path) -> (expires_at, response_bytes)
cache = {}
cache_lock = threading.Lock()


# look up a cached response, dropping it if expired
def cache_get(key):
  with cache_lock:
    entry = cache.get(key)
    if entry is None:
      return None
    expires_at, response = entry
    if time.time() >= expires_at:
      del cache[key]
      return None
    return response


def cache_put(key, response):
  with cache_lock:
    cache[key] = (time.time() + CACHE_TTL, response)


# pull the status code out of an http response, e.g. b"HTTP/1.1 200 OK\r\n..." -> 200
# returns None if we can't parse it
def parse_status_code(response):
  try:
    first_line = response.split(b"\r\n", 1)[0].decode("iso-8859-1")
    return int(first_line.split(" ")[1])
  except (IndexError, ValueError, UnicodeDecodeError):
    return None


# send a minimal http error response back to the client
# best-effort, we swallow socket errors here because the client may already be gone
def send_error(conn, status, reason, detail=""):
  body = f"{status} {reason}\n{detail}\n".encode("iso-8859-1")
  response = (
    f"HTTP/1.1 {status} {reason}\r\n"
    f"Content-Type: text/plain; charset=iso-8859-1\r\n"
    f"Content-Length: {len(body)}\r\n"
    f"Connection: close\r\n"
    f"\r\n"
  ).encode("iso-8859-1") + body
  try:
    conn.sendall(response)
  except socket.error:
    pass


# read until we hit the blank line that ends the headers
# returns (headers, body) as bytes, or None if the client closed first
def read_http_request(conn):
  buffer = b""
  while b"\r\n\r\n" not in buffer:
    chunk = conn.recv(RECV_CHUNK)
    if not chunk:
      return None
    buffer += chunk
  headers_end = buffer.index(b"\r\n\r\n") + 4
  return buffer[:headers_end], buffer[headers_end:]


# pull method, url, and version out of the first line
# returns None if we can't parse it
def parse_request_line(headers_bytes):
  try:
    first_line = headers_bytes.split(b"\r\n", 1)[0].decode("iso-8859-1")
    parts = first_line.split(" ")
    if len(parts) != 3:
      return None
    return parts[0], parts[1], parts[2]
  except (UnicodeDecodeError, IndexError):
    return None


# look up a header by name, ignoring case
# returns the value as a string, or None if the header isn't there
def get_header(headers_bytes, name):
  lines = headers_bytes.split(b"\r\n")
  prefix = name.lower().encode("iso-8859-1") + b":"
  for line in lines[1:]:
    if line.lower().startswith(prefix):
      return line[len(prefix):].strip().decode("iso-8859-1")
  return None


# figure out what host, port, and path to forward to
# prefers the absolute url in the request line, otherwise uses the Host header
def resolve_destination(url, headers_bytes):
  if url.startswith("http://") or url.startswith("https://"):
    split = urlsplit(url)
    host = split.hostname
    port = split.port or 80
    path = split.path or "/"
    if split.query:
      path += "?" + split.query
    return host, port, path

  host_header = get_header(headers_bytes, "Host")
  if not host_header:
    return None
  if ":" in host_header:
    host, port_str = host_header.rsplit(":", 1)
    try:
      port = int(port_str)
    except ValueError:
      port = 80
  else:
    host, port = host_header, 80
  return host, port, url or "/"


# swap the first line so the origin sees a relative path
# origin servers want "GET / HTTP/1.1" instead of the absolute url proxies get
def rewrite_request(headers_bytes, method, path, version):
  rest = headers_bytes.split(b"\r\n", 1)[1] if b"\r\n" in headers_bytes else b""
  new_first_line = f"{method} {path} {version}".encode("iso-8859-1")
  return new_first_line + b"\r\n" + rest


# keep reading from the origin until it closes the connection or we time out
def read_full_response(origin_sock):
  chunks = []
  while True:
    try:
      chunk = origin_sock.recv(RECV_CHUNK)
    except socket.timeout:
      break
    if not chunk:
      break
    chunks.append(chunk)
  return b"".join(chunks)


def handle_one_request(conn, addr):
  print(f"[NEW CONNECTION] {addr} connected.")
  try:
    try:
      parsed = read_http_request(conn)
    except socket.error as e:
      print(f"[ERROR] {addr}: read failed: {e}")
      return
    if parsed is None:
      print(f"[ERROR] {addr}: client closed before headers complete")
      return
    headers_bytes, body = parsed

    request_line = parse_request_line(headers_bytes)
    if request_line is None:
      print(f"[ERROR] {addr}: malformed request line")
      send_error(conn, 400, "Bad Request", "could not parse request line")
      return
    method, url, version = request_line
    print(f"[REQUEST] {method} {url} {version}")

    dest = resolve_destination(url, headers_bytes)
    if dest is None:
      print(f"[ERROR] {addr}: could not resolve destination host")
      send_error(conn, 400, "Bad Request", "missing Host header and no absolute url")
      return
    host, port, path = dest

    cache_key = (host, port, path) if method == "GET" else None
    if cache_key is not None:
      cached = cache_get(cache_key)
      if cached is not None:
        print(f"[CACHE HIT] {host}:{port}{path} ({len(cached)} bytes)")
        conn.sendall(cached)
        return

    print(f"[FORWARD] {host}:{port}{path}")
    forwarded = rewrite_request(headers_bytes, method, path, version) + body

    origin = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    origin.settimeout(ORIGIN_TIMEOUT)
    try:
      try:
        origin.connect((host, port))
      except socket.gaierror as e:
        print(f"[ERROR] {addr}: dns lookup failed for {host}: {e}")
        send_error(conn, 502, "Bad Gateway", f"could not resolve {host}")
        return
      except socket.timeout:
        print(f"[ERROR] {addr}: connect to {host}:{port} timed out")
        send_error(conn, 504, "Gateway Timeout", f"timed out connecting to {host}")
        return
      except socket.error as e:
        print(f"[ERROR] {addr}: connect to {host}:{port} failed: {e}")
        send_error(conn, 502, "Bad Gateway", f"could not connect to {host}:{port}")
        return

      try:
        origin.sendall(forwarded)
        response = read_full_response(origin)
      except socket.error as e:
        print(f"[ERROR] {addr}: origin io failed: {e}")
        send_error(conn, 502, "Bad Gateway", "origin connection failed mid-request")
        return
    finally:
      origin.close()

    print(f"[RESPONSE] {len(response)} bytes from {host}:{port}")
    try:
      conn.sendall(response)
    except socket.error as e:
      print(f"[ERROR] {addr}: client write failed: {e}")
      return

    if cache_key is not None and parse_status_code(response) == 200:
      cache_put(cache_key, response)
      print(f"[CACHE STORE] {host}:{port}{path}")

  finally:
    conn.close()
    print(f"[DISCONNECTED] {addr}")


def main():
  try:
    port = int(sys.argv[1])
    if not (1 <= port <= 65535):
      port = DEFAULT_PORT
  except (IndexError, ValueError):
    port = DEFAULT_PORT

  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  server.bind((LISTEN_HOST, port))
  server.listen()

  print(f"[STARTING] Proxy listening on {LISTEN_HOST}:{port}")

  try:
    while True:
      conn, addr = server.accept()
      worker = threading.Thread(
        target=handle_one_request,
        args=(conn, addr),
        daemon=True,
      )
      worker.start()
      print(f"[ACTIVE THREADS] {threading.active_count() - 1}")
  except KeyboardInterrupt:
    print("\n[SHUTTING DOWN] Proxy stopping.")
  finally:
    server.close()


if __name__ == "__main__":
  main()
