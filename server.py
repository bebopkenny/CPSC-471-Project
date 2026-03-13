import sys
import socket
import threading

HEADER = 64
DEFAULT_PORT = 5050
SERVER = socket.gethostbyname(socket.gethostname())
FORMAT = "utf-8"
DISCONNECT_MESSAGE = "!DISCONNECT"

clients = {}       # addr -> conn
clients_lock = threading.Lock()


def send_to_client(conn, msg):
  """Push a message from the server to a specific client."""
  try:
    message = msg.encode(FORMAT)
    msg_length = str(len(message)).encode(FORMAT)
    msg_length += b' ' * (HEADER - len(msg_length))
    conn.send(msg_length)
    conn.send(message)
  except socket.error as e:
    print(f"[ERROR] Could not send to client: {e}")


def broadcast(msg):
  """Send a message to all connected clients."""
  with clients_lock:
    for addr, conn in list(clients.items()):
      send_to_client(conn, f"[SERVER BROADCAST] {msg}")


def handle_client(conn, addr):
  print(f"[NEW CONNECTION] {addr} connected.")
  with clients_lock:
    clients[addr] = conn

  connected = True
  while connected:
    try:
      msg_length = conn.recv(HEADER).decode(FORMAT) # tells us how long the message is that is coming
      if msg_length:
        msg_length = int(msg_length) # use that and convert it into an integer
        msg = conn.recv(msg_length).decode(FORMAT) # how many bits we will be receiving for the actual message
        if msg == DISCONNECT_MESSAGE:
          connected = False

        print(f"[{addr}] {msg}")
        ack = "Msg received".encode(FORMAT)
        ack_length = str(len(ack)).encode(FORMAT)
        ack_length += b' ' * (HEADER - len(ack_length))
        conn.send(ack_length)
        conn.send(ack)
    except socket.error as e:
      print(f"[ERROR] {addr}: {e}")
      break

  with clients_lock:
    clients.pop(addr, None)
  conn.close()
  print(f"[DISCONNECTED] {addr} disconnected.")


def server_command_loop():
  """
  Read commands from the server's terminal and dispatch them.

  Commands:
    !all <message>        — broadcast to all clients
    !msg <ip> <port> <message> — send to one client by address
    !kick <ip> <port>     — disconnect a specific client
    !list                 — print connected clients
    !help                 — show this help
  """
  print("Server command prompt ready. Type !help for commands.")
  while True:
    try:
      raw = input()
    except EOFError:
      break

    if not raw.strip():
      continue

    parts = raw.strip().split(" ", 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "!all":
      if arg:
        broadcast(arg)
        print(f"[BROADCAST] {arg}")
      else:
        print("Usage: !all <message>")

    elif cmd == "!msg":
      # expects: !msg <ip> <port> <message>
      tokens = arg.split(" ", 2)
      if len(tokens) < 3:
        print("Usage: !msg <ip> <port> <message>")
        continue
      ip, port_str, message = tokens
      try:
        target = (ip, int(port_str))
      except ValueError:
        print("Invalid port number.")
        continue
      with clients_lock:
        conn = clients.get(target)
      if conn:
        send_to_client(conn, f"[SERVER] {message}")
        print(f"[SENT -> {target}] {message}")
      else:
        print(f"No client found at {target}.")

    elif cmd == "!kick":
      tokens = arg.split(" ", 1)
      if len(tokens) < 2:
        print("Usage: !kick <ip> <port>")
        continue
      ip, port_str = tokens
      try:
        target = (ip, int(port_str))
      except ValueError:
        print("Invalid port number.")
        continue
      with clients_lock:
        conn = clients.get(target)
      if conn:
        send_to_client(conn, "[SERVER] You have been kicked.")
        conn.close()
        print(f"[KICKED] {target}")
      else:
        print(f"No client found at {target}.")

    elif cmd == "!list":
      with clients_lock:
        if clients:
          for addr in clients:
            print(f"  {addr}")
        else:
          print("No clients connected.")

    elif cmd == "!help":
      print(
        "Commands:\n"
        "  !all <message>             — broadcast to all clients\n"
        "  !msg <ip> <port> <message> — send to one client\n"
        "  !kick <ip> <port>          — disconnect a client\n"
        "  !list                      — list connected clients\n"
        "  !help                      — show this help"
      )

    else:
      print(f"Unknown command '{cmd}'. Type !help for commands.")


def start(server):
  try:
    server.listen()
    print(f"[LISTENING] Server is listening on {SERVER}")
    print(f"Type !help for a list of commmands")
    while True:
      conn, addr = server.accept()
      thread = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
      thread.start()
      print(f"[ACTIVE CONNECTIONS] {threading.active_count() - 1}")
  except KeyboardInterrupt:
    print("\n[SHUTTING DOWN] Server stopping.")
    server.close()


def main():
  try:
    port = int(sys.argv[1])
    if not (1 <= port <= 9999):
      port = DEFAULT_PORT
  except (IndexError, ValueError):
    port = DEFAULT_PORT

  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.bind((SERVER, port))

  print(f"Using port {port}")
  print("[STARTING] Server is starting...")

  # Command loop runs on the main thread; networking runs in the background.
  listener_thread = threading.Thread(target=start, args=(server,), daemon=True)
  listener_thread.start()

  server_command_loop()


if __name__ == "__main__":
  main()