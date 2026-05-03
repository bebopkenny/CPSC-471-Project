import sys
import time
import signal
import socket
import threading
from urllib.parse import urlsplit

DEFAULT_PORT = 8080
LISTEN_HOST = "0.0.0.0"
RECV_CHUNK = 4096
ORIGIN_TIMEOUT = 10
CLIENT_TIMEOUT = 30
SHUTDOWN_DEADLINE = 10
CACHE_TTL = 60

# rfc 2616: proxies must strip hop-by-hop headers when forwarding
HOP_BY_HOP = (
  b"connection",
  b"proxy-connection",
  b"keep-alive",
  b"te",
  b"trailer",
  b"transfer-encoding",
  b"upgrade",
  b"proxy-authenticate",
  b"proxy-authorization",
)

# (host, port, path) -> (expires_at, response_bytes)
cache = {}
cache_lock = threading.Lock()

# track in-flight workers so we can drain them on shutdown
workers = set()
workers_lock = threading.Lock()

# set by SIGINT handler so the accept loop knows to stop
# we use an Event + accept timeout because on macOS python a blocked accept()
# does not get interrupted by SIGINT
shutdown_event = threading.Event()


def install_signal_handler():
  signal.signal(signal.SIGINT, lambda *_: shutdown_event.set())
  signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())

# serialize log writes so concurrent threads don't interleave lines
log_lock = threading.Lock()


def log(tag, message):
  ts = time.strftime("%Y-%m-%d %H:%M:%S")
  thread = threading.current_thread().name
  with log_lock:
    print(f"{ts} [{thread}] [{tag}] {message}", flush=True)


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


# swap the first line so the origin sees a relative path, and force Connection: close
# origin servers want "GET / HTTP/1.1" instead of the absolute url proxies get
# we also drop client keep-alive headers and add Connection: close so the origin
# closes the socket as soon as the response is done, instead of making us wait
# for ORIGIN_TIMEOUT on every request
def rewrite_request(headers_bytes, method, path, version):
  parts = headers_bytes.split(b"\r\n")
  # parts[0] is the request line, the last two entries are empty (from the trailing \r\n\r\n)
  # everything between is a header line
  filtered = []
  for line in parts[1:-2]:
    name = line.split(b":", 1)[0].strip().lower()
    if name in HOP_BY_HOP:
      continue
    filtered.append(line)
  filtered.append(b"Connection: close")
  new_first_line = f"{method} {path} {version}".encode("iso-8859-1")
  return new_first_line + b"\r\n" + b"\r\n".join(filtered) + b"\r\n\r\n"


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
  start = time.time()
  conn.settimeout(CLIENT_TIMEOUT)
  log("CONN", f"open from {addr[0]}:{addr[1]}")
  method = url = host = path = None
  port = 0
  status = None
  bytes_out = 0
  cache_state = "skip"

  try:
    try:
      parsed = read_http_request(conn)
    except socket.error as e:
      log("ERROR", f"read failed: {e}")
      return
    if parsed is None:
      log("ERROR", "client closed before headers complete")
      return
    headers_bytes, body = parsed

    request_line = parse_request_line(headers_bytes)
    if request_line is None:
      log("ERROR", "malformed request line")
      send_error(conn, 400, "Bad Request", "could not parse request line")
      status = 400
      return
    method, url, version = request_line
    log("REQ", f"{method} {url} {version}")

    dest = resolve_destination(url, headers_bytes)
    if dest is None:
      log("ERROR", "could not resolve destination host")
      send_error(conn, 400, "Bad Request", "missing Host header and no absolute url")
      status = 400
      return
    host, port, path = dest

    cache_key = (host, port, path) if method == "GET" else None
    if cache_key is not None:
      cached = cache_get(cache_key)
      if cached is not None:
        log("CACHE", f"hit {host}:{port}{path} ({len(cached)} bytes)")
        conn.sendall(cached)
        status = parse_status_code(cached)
        bytes_out = len(cached)
        cache_state = "hit"
        return

    log("FWD", f"{host}:{port}{path}")
    forwarded = rewrite_request(headers_bytes, method, path, version) + body

    origin = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    origin.settimeout(ORIGIN_TIMEOUT)
    try:
      try:
        origin.connect((host, port))
      except socket.gaierror as e:
        log("ERROR", f"dns lookup failed for {host}: {e}")
        send_error(conn, 502, "Bad Gateway", f"could not resolve {host}")
        status = 502
        return
      except socket.timeout:
        log("ERROR", f"connect to {host}:{port} timed out")
        send_error(conn, 504, "Gateway Timeout", f"timed out connecting to {host}")
        status = 504
        return
      except socket.error as e:
        log("ERROR", f"connect to {host}:{port} failed: {e}")
        send_error(conn, 502, "Bad Gateway", f"could not connect to {host}:{port}")
        status = 502
        return

      try:
        origin.sendall(forwarded)
        response = read_full_response(origin)
      except socket.error as e:
        log("ERROR", f"origin io failed: {e}")
        send_error(conn, 502, "Bad Gateway", "origin connection failed mid-request")
        status = 502
        return
    finally:
      origin.close()

    status = parse_status_code(response)
    bytes_out = len(response)
    log("RES", f"{status} {bytes_out} bytes from {host}:{port}")

    try:
      conn.sendall(response)
    except socket.error as e:
      log("ERROR", f"client write failed: {e}")
      return

    if cache_key is not None and status == 200:
      cache_put(cache_key, response)
      cache_state = "store"
      log("CACHE", f"store {host}:{port}{path}")
    elif cache_key is not None:
      cache_state = "miss"

  finally:
    conn.close()
    elapsed_ms = int((time.time() - start) * 1000)
    log(
      "SUMMARY",
      f"{method or '-'} {url or '-'} -> {status if status is not None else '-'} "
      f"{bytes_out}b cache={cache_state} {elapsed_ms}ms",
    )
    with workers_lock:
      workers.discard(threading.current_thread())


def main():
  try:
    port = int(sys.argv[1])
    if not (1 <= port <= 65535):
      port = DEFAULT_PORT
  except (IndexError, ValueError):
    port = DEFAULT_PORT

  install_signal_handler()

  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  server.bind((LISTEN_HOST, port))
  server.listen()
  # short timeout so accept() periodically wakes up and we can check shutdown_event
  server.settimeout(1.0)

  log("START", f"proxy listening on {LISTEN_HOST}:{port}")

  conn_count = 0
  try:
    while not shutdown_event.is_set():
      try:
        conn, addr = server.accept()
      except socket.timeout:
        continue
      conn_count += 1
      worker = threading.Thread(
        target=handle_one_request,
        args=(conn, addr),
        name=f"worker-{conn_count}",
      )
      with workers_lock:
        workers.add(worker)
      worker.start()
      log("ACCEPT", f"active workers: {len(workers)}")

    log("STOP", "proxy shutting down, draining in-flight workers")
    deadline = time.time() + SHUTDOWN_DEADLINE
    with workers_lock:
      snapshot = list(workers)
    for w in snapshot:
      remaining = deadline - time.time()
      if remaining <= 0:
        break
      w.join(timeout=remaining)
    with workers_lock:
      stragglers = len(workers)
    if stragglers:
      log("STOP", f"timed out with {stragglers} workers still running")
    else:
      log("STOP", "shutdown complete")
  finally:
    server.close()


if __name__ == "__main__":
  main()