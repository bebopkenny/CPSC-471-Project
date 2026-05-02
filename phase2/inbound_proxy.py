import os
import sys
import time
import signal
import socket
import threading

DEFAULT_PORT = 8081
LISTEN_HOST = "0.0.0.0"

# the data server is a fixed backend on the same host, loopback only.
# the inbound proxy is the only public face of the data server.
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 9000

# auth model: anything under PUBLIC_PREFIXES is open to everyone.
# anything else needs X-Auth-Token to match the secret in PROXY_AUTH_TOKEN.
# the secret is read from the environment at startup so it never lives in source.
# main() exits early if the env var is not set.
AUTH_TOKEN_ENV = "PROXY_AUTH_TOKEN"
AUTH_HEADER = b"x-auth-token"
PUBLIC_PREFIXES = ("/public/",)
AUTH_TOKEN = None

RECV_CHUNK = 4096
ORIGIN_TIMEOUT = 10
CLIENT_TIMEOUT = 30
SHUTDOWN_DEADLINE = 10

# rfc 2616: hop-by-hop headers must be stripped when forwarding through a proxy
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

# also strip the auth header so the secret never reaches the backend logs
STRIP_BEFORE_FORWARD = HOP_BY_HOP + (AUTH_HEADER,)

shutdown_event = threading.Event()
workers = set()
workers_lock = threading.Lock()
log_lock = threading.Lock()


def install_signal_handler():
  signal.signal(signal.SIGINT, lambda *_: shutdown_event.set())
  signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())


def log(tag, message):
  ts = time.strftime("%Y-%m-%d %H:%M:%S")
  thread = threading.current_thread().name
  with log_lock:
    print(f"{ts} [{thread}] [{tag}] {message}", flush=True)


def read_http_request(conn):
  buffer = b""
  while b"\r\n\r\n" not in buffer:
    chunk = conn.recv(RECV_CHUNK)
    if not chunk:
      return None
    buffer += chunk
  headers_end = buffer.index(b"\r\n\r\n") + 4
  return buffer[:headers_end], buffer[headers_end:]


def parse_request_line(headers_bytes):
  try:
    first_line = headers_bytes.split(b"\r\n", 1)[0].decode("iso-8859-1")
    parts = first_line.split(" ")
    if len(parts) != 3:
      return None
    return parts[0], parts[1], parts[2]
  except (UnicodeDecodeError, IndexError):
    return None


def get_header(headers_bytes, name_lower_bytes):
  prefix = name_lower_bytes + b":"
  for line in headers_bytes.split(b"\r\n")[1:]:
    if line.lower().startswith(prefix):
      return line[len(prefix):].strip().decode("iso-8859-1")
  return None


def send_error(conn, status, reason, detail=""):
  body = f"{status} {reason}\n{detail}\n".encode("utf-8")
  response = (
    f"HTTP/1.1 {status} {reason}\r\n"
    f"Content-Type: text/plain; charset=utf-8\r\n"
    f"Content-Length: {len(body)}\r\n"
    f"Connection: close\r\n"
    f"\r\n"
  ).encode("iso-8859-1") + body
  try:
    conn.sendall(response)
  except socket.error:
    pass


def parse_status_code(response):
  try:
    first_line = response.split(b"\r\n", 1)[0].decode("iso-8859-1")
    return int(first_line.split(" ")[1])
  except (IndexError, ValueError, UnicodeDecodeError):
    return None


# rebuild the request to send to the backend: keep the same path and headers
# minus the strip set, force connection: close so the backend hangs up after
# the response and we do not have to wait for the read timeout.
def rewrite_for_backend(headers_bytes, method, path, version):
  parts = headers_bytes.split(b"\r\n")
  filtered = []
  for line in parts[1:-2]:
    name = line.split(b":", 1)[0].strip().lower()
    if name in STRIP_BEFORE_FORWARD:
      continue
    filtered.append(line)
  filtered.append(b"Connection: close")
  new_first_line = f"{method} {path} {version}".encode("iso-8859-1")
  return new_first_line + b"\r\n" + b"\r\n".join(filtered) + b"\r\n\r\n"


def read_full_response(sock):
  chunks = []
  while True:
    try:
      chunk = sock.recv(RECV_CHUNK)
    except socket.timeout:
      break
    if not chunk:
      break
    chunks.append(chunk)
  return b"".join(chunks)


# returns (allowed, reason). reason is logged either way and used to pick
# the right error status when denied.
def check_access(path, headers_bytes):
  if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
    return True, "public"
  token = get_header(headers_bytes, AUTH_HEADER)
  if token is None:
    return False, "missing token"
  if token != AUTH_TOKEN:
    return False, "invalid token"
  return True, "authenticated"


def handle_one_request(conn, addr):
  start = time.time()
  conn.settimeout(CLIENT_TIMEOUT)
  log("CONN", f"open from {addr[0]}:{addr[1]}")
  method = path = None
  status = None
  bytes_out = 0
  decision = "skip"

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
      send_error(conn, 400, "Bad Request", "could not parse request line")
      status = 400
      return
    method, path, version = request_line
    log("REQ", f"{method} {path}")

    allowed, reason = check_access(path, headers_bytes)
    decision = reason
    if not allowed:
      log("DENY", f"{path}: {reason}")
      if reason == "missing token":
        send_error(conn, 401, "Unauthorized", "this path requires X-Auth-Token")
        status = 401
      else:
        send_error(conn, 403, "Forbidden", reason)
        status = 403
      return

    forwarded = rewrite_for_backend(headers_bytes, method, path, version) + body

    backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    backend.settimeout(ORIGIN_TIMEOUT)
    try:
      try:
        backend.connect((BACKEND_HOST, BACKEND_PORT))
      except socket.error as e:
        log("ERROR", f"backend connect failed: {e}")
        send_error(conn, 502, "Bad Gateway", "data server unreachable")
        status = 502
        return
      try:
        backend.sendall(forwarded)
        response = read_full_response(backend)
      except socket.error as e:
        log("ERROR", f"backend io failed: {e}")
        send_error(conn, 502, "Bad Gateway", "data server io failed")
        status = 502
        return
    finally:
      backend.close()

    status = parse_status_code(response)
    bytes_out = len(response)
    log("RES", f"{status} {bytes_out} bytes from backend")

    try:
      conn.sendall(response)
    except socket.error as e:
      log("ERROR", f"client write failed: {e}")
      return

  finally:
    conn.close()
    elapsed_ms = int((time.time() - start) * 1000)
    log(
      "SUMMARY",
      f"{method or '-'} {path or '-'} -> {status if status is not None else '-'} "
      f"{bytes_out}b auth={decision} {elapsed_ms}ms",
    )
    with workers_lock:
      workers.discard(threading.current_thread())


def main():
  global AUTH_TOKEN
  AUTH_TOKEN = os.environ.get(AUTH_TOKEN_ENV)
  if not AUTH_TOKEN:
    print(f"{AUTH_TOKEN_ENV} env var is not set, refusing to start")
    print(f"set it with: {AUTH_TOKEN_ENV}=<secret> python3 phase2/inbound_proxy.py [port]")
    sys.exit(1)

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
  server.settimeout(1.0)

  log("START", f"inbound proxy listening on {LISTEN_HOST}:{port}, backend={BACKEND_HOST}:{BACKEND_PORT}")

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

    log("STOP", "inbound proxy shutting down, draining workers")
    deadline = time.time() + SHUTDOWN_DEADLINE
    with workers_lock:
      snapshot = list(workers)
    for w in snapshot:
      remaining = deadline - time.time()
      if remaining <= 0:
        break
      w.join(timeout=remaining)
    log("STOP", "shutdown complete")
  finally:
    server.close()


if __name__ == "__main__":
  main()
