import sys
import socket
import threading # creates multiple threads, while one piece of code is waiting the other is running

HEADER = 64
DEFAULT_PORT = 5050 # port that is not being used for anything else
SERVER = socket.gethostbyname(socket.gethostname()) # gets the ip address
# ADDR = (SERVER, PORT)
FORMAT = "utf-8"
DISCONNECT_MESSAGE = "!DISCONNECT"

def handle_client(conn, addr):
  print(f"[NEW CONNECTION] {addr} connected.")
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
        conn.send("Msg received".encode(FORMAT))
    except socket.error as e:
      print(f"Failed: {e}")
      conn.close()
      return
  conn.close()

def start(server):
  try:
    server.listen() # listening for new connections
    print(f"[LISTENING] Server is listening {SERVER}")
    while True: # it will continue to listen until we don't want it to
      conn, addr = server.accept()
      thread = threading.Thread(target=handle_client, args=(conn, addr))
      thread.start()
      print(f"[ACTIVE CONNECTIONS] {threading.active_count() - 1}")
  except KeyboardInterrupt:
    print("\n [SHUTTING DOWN] Sever stopping")
    server.close()

def main():
  try:
    port = int(sys.argv[1])
    if not (1 <= port <= 9999):
      port = DEFAULT_PORT
  except (IndexError, ValueError, NameError):
    port = DEFAULT_PORT
  
  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.bind((SERVER, port))

  print(f"Using port {port}")
  print("[STARTING] server is starting...")
  start(server)

if __name__ == "__main__":
  main()
