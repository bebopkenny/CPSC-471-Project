import os
import sys
import time
import signal
import socket
import threading

# bind to loopback only so this is only reachable from the inbound proxy
# running on the same host. nothing on the public internet can hit this directly.
DATA_HOST = "127.0.0.1"
DEFAULT_PORT = 9000
DATA_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

RECV_CHUNK = 4096
CLIENT_TIMEOUT = 30
SHUTDOWN_DEADLINE = 5

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


# read until the blank line that ends the headers. body is ignored, this server
# only handles GET so we never look at request bodies.
def read_http_request(conn):
  buffer = b""
  while b"\r\n\r\n" not in buffer:
    chunk = conn.recv(RECV_CHUNK)
    if not chunk:
      return None
    buffer += chunk
  return buffer


def parse_request_line(headers_bytes):
  try:
    first_line = headers_bytes.split(b"\r\n", 1)[0].decode("iso-8859-1")
    parts = first_line.split(" ")
    if len(parts) != 3:
      return None
    return parts[0], parts[1], parts[2]
  except (UnicodeDecodeError, IndexError):
    return None


def send_response(conn, status, reason, body, content_type="text/plain; charset=utf-8"):
  if isinstance(body, str):
    body = body.encode("utf-8")
  headers = (
    f"HTTP/1.1 {status} {reason}\r\n"
    f"Content-Type: {content_type}\r\n"
    f"Content-Length: {len(body)}\r\n"
    f"Connection: close\r\n"
    f"\r\n"
  ).encode("iso-8859-1")
  try:
    conn.sendall(headers + body)
  except socket.error:
    pass


# resolve a url path to a real file inside DATA_ROOT.
# returns the absolute path, or None if the request tries to escape the root
# via .. or absolute paths. this is the only thing standing between a sloppy
# request and reading /etc/passwd.
def safe_path(url_path):
  rel = url_path.lstrip("/")
  if not rel:
    return None
  candidate = os.path.normpath(os.path.join(DATA_ROOT, rel))
  if candidate != DATA_ROOT and not candidate.startswith(DATA_ROOT + os.sep):
    return None
  return candidate


def guess_content_type(path):
  ext = os.path.splitext(path)[1].lower()
  table = {
    ".txt": "text/plain; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript",
  }
  return table.get(ext, "application/octet-stream")


def handle_one_request(conn, addr):
  start = time.time()
  conn.settimeout(CLIENT_TIMEOUT)
  log("CONN", f"open from {addr[0]}:{addr[1]}")
  method = url = None
  status = None
  bytes_out = 0

  try:
    try:
      buffer = read_http_request(conn)
    except socket.error as e:
      log("ERROR", f"read failed: {e}")
      return
    if buffer is None:
      log("ERROR", "client closed before request complete")
      return

    request_line = parse_request_line(buffer)
    if request_line is None:
      send_response(conn, 400, "Bad Request", "could not parse request line\n")
      status = 400
      return
    method, url, _ = request_line
    log("REQ", f"{method} {url}")

    if method != "GET":
      send_response(conn, 405, "Method Not Allowed", f"only GET supported, got {method}\n")
      status = 405
      return

    path = safe_path(url)
    if path is None:
      log("DENY", f"blocked path traversal attempt: {url}")
      send_response(conn, 400, "Bad Request", "invalid path\n")
      status = 400
      return

    if not os.path.isfile(path):
      log("MISS", f"not found: {url}")
      send_response(conn, 404, "Not Found", f"{url} not found\n")
      status = 404
      return

    try:
      with open(path, "rb") as f:
        body = f.read()
    except OSError as e:
      log("ERROR", f"read failed for {path}: {e}")
      send_response(conn, 500, "Internal Server Error", "could not read file\n")
      status = 500
      return

    bytes_out = len(body)
    status = 200
    log("SERVE", f"{url} ({bytes_out} bytes)")
    send_response(conn, 200, "OK", body, content_type=guess_content_type(path))

  finally:
    conn.close()
    elapsed_ms = int((time.time() - start) * 1000)
    log(
      "SUMMARY",
      f"{method or '-'} {url or '-'} -> {status if status is not None else '-'} "
      f"{bytes_out}b {elapsed_ms}ms",
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

  if not os.path.isdir(DATA_ROOT):
    print(f"data root not found: {DATA_ROOT}")
    sys.exit(1)

  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  server.bind((DATA_HOST, port))
  server.listen()
  server.settimeout(1.0)

  log("START", f"data server listening on {DATA_HOST}:{port}, root={DATA_ROOT}")

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
        name=f"data-{conn_count}",
      )
      with workers_lock:
        workers.add(worker)
      worker.start()

    log("STOP", "data server shutting down, draining workers")
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
