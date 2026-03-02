import socket
import threading # creates multiple threads, while one piece of code is waiting the other is running

HEADER = 64
PORT = 5050 # port that is not being used for anything else
SERVER = socket.gethostbyname(socket.gethostname()) # gets the ip address
ADDR = (SERVER, PORT)
FORMAT = "utf-8"
DISCONNECT_MESSAGE = "!DISCONNECT"


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # first param. it is through the internet and the second is the type
server.bind(ADDR)

def handle_client(conn, addr):
  print(f"[NEW CONNECTION] {addr} connected.")

  connected = True
  while connected:
    try:
      msg_length = conn.recv(HEADER).decode(FORMAT) # tells us how long the message is that is comming
      if msg_length:
        msg_length = int(msg_length) # use that and convert it into an integer
        msg = conn.recv(msg_length).decode(FORMAT) # how many bits we will be reciving for the actual message
        if msg == DISCONNECT_MESSAGE:
          connected = False

        print(f"[{addr}] {msg}")
        conn.send("Msg recieved".encode(FORMAT))
    except socket.error as e:
      print(f"Failed: {e}")
      conn.close()
      return
  conn.close()

def start():
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

print("[STARTING] server is starting...")
start()