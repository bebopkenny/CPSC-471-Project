import socket
import sys

HEADER = 64
DEFAULT_PORT = 5050
FORMAT = "utf-8"
DISCONNECT_MESSAGE = "!DISCONNECT"
SERVER = socket.gethostbyname(socket.gethostname())
# ADDR = (SERVER, PORT)

def send(client, msg):
  message = msg.encode(FORMAT) # whenever we send messages we need to encode first 
  msg_length = len(message)
  send_length = str(msg_length).encode(FORMAT)
  send_length += b' ' * (HEADER - len(send_length))
  
  try:
    client.send(send_length)
    client.send(message)
    print(client.recv(2048).decode(FORMAT))
  except socket.error as e:
    print(f"Failed: {e}")
    sys.exit()

def main():
  try:
    port = int(sys.argv[1])
    if not (1 <= port <= 9999):
      port = DEFAULT_PORT
  except (IndexError, ValueError, NameError):
    port = DEFAULT_PORT
  
  client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

  try:
    client.connect((SERVER, port))
    print("Connected to sever")
  except socket.error as e:
    print(f"Connection error: {e}")
    sys.exit()

  print("Connected to server. To disconnect from server: enter 'disconnect'")

  while True:
    message = input("Enter text: ")
    if message == "disconnect":
      send(client, DISCONNECT_MESSAGE)
      break
    send(client, message)

if __name__ == "__main__":
  main()
