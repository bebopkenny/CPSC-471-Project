import sys
import socket
import threading

HEADER = 64
DEFAULT_PORT = 5050
FORMAT = "utf-8"
DISCONNECT_MESSAGE = "!DISCONNECT"
SERVER = socket.gethostbyname(socket.gethostname())


def receive_loop(client):
  """
  Background thread: continuously listens for server-pushed messages
  and prints them without interrupting the user's input prompt.
  """
  while True:
    try:
      msg_length = client.recv(HEADER).decode(FORMAT)
      if not msg_length:
        break
      msg_length = int(msg_length.strip())
      msg = client.recv(msg_length).decode(FORMAT)
      # Print on its own line so it doesn't tangle with the input prompt.
      if msg == "Msg received":
        print(f"---{msg}---\n\n", end="", flush = True)
      else:
        print(f"\n{msg}\nEnter text: ", end="", flush=True)
    except socket.error:
      print("\n[DISCONNECTED] Lost connection to server.")
      break


def send(client, msg):
  message = msg.encode(FORMAT)
  msg_length = str(len(message)).encode(FORMAT)
  msg_length += b' ' * (HEADER - len(msg_length))

  try:
    client.send(msg_length)
    client.send(message)
  except socket.error as e:
    print(f"Failed: {e}")
    sys.exit()


def main():
  try:
    port = int(sys.argv[1])
    if not (1 <= port <= 9999):
      port = DEFAULT_PORT
  except (IndexError, ValueError):
    port = DEFAULT_PORT

  client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

  try:
    client.connect((SERVER, port))
  except socket.error as e:
    print(f"Connection error: {e}")
    sys.exit()

  print("Connected to server. To disconnect: enter 'disconnect'")

  # Start the background thread that listens for server-initiated messages.
  recv_thread = threading.Thread(target=receive_loop, args=(client,), daemon=True)
  recv_thread.start()

  while True:
    message = input("Enter text: ")
    if message == "disconnect":
      send(client, DISCONNECT_MESSAGE)
      break
    send(client, message)

  client.close()


if __name__ == "__main__":
  main()
