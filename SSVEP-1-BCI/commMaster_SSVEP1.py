import socket
import threading
import argparse
import pickle
import struct
import time
import os
import signal
import sys

from utilities.detectedObjects import DetectedObject

# Variabili globali
DETECTED = []
OPENVIBE_PORTS = [5680, 5681, 5682]
ROBOTVISION_PORT = 5683
ROS_PORT = 8080
LABEL_DETECTION_VALUE = {OPENVIBE_PORTS[0]: 0.00, OPENVIBE_PORTS[1]: 0.00, OPENVIBE_PORTS[2]: 0.00}
PORT2LABEL = {OPENVIBE_PORTS[0]: "LABEL_00_12Hz", OPENVIBE_PORTS[1]: "LABEL_01_15Hz", OPENVIBE_PORTS[2]: "LABEL_02_20Hz"}
LABEL2FREQ = {"LABEL_00_12Hz": 12, "LABEL_01_15Hz": 15, "LABEL_02_20Hz": 20}
CONFIDENCE_THRESHOLD = 0.1
SENDER_FLAG_1 = False # Gestisce l'invio multiplo di comandi
SENDER_FLAG_2 = True # Gestisce la risposta da parte di ROS
terminate_flag = False  # Flag per terminare i thread in modo sicuro

# Funzione per catturare SIGINT (Ctrl+C) e chiudere in modo sicuro
def signal_handler(sig, frame):
    global terminate_flag
    print("\n[INFO] Interruzione rilevata, chiusura delle connessioni...")
    terminate_flag = True
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def findDetectedObject(objects, label_to_find):
    for obj in objects:
        if obj.freq == label_to_find:
            return obj
    return None  

def recv_exact(sock, size):
    buffer = b''
    while len(buffer) < size:
        try:
            data = sock.recv(size - len(buffer))
        except socket.error as e:
            print(f"[x] Errore di ricezione dati: {e}")
            return None
        if not data:
            break
        buffer += data
    return buffer

def client_thread(port):
    global terminate_flag
    while not terminate_flag:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(('localhost', port))
                print(f"[INFO] Connected to OpenVIBE through port {port}")
                while not terminate_flag:
                    data = recv_exact(s, 16)
                    if data:
                        detected, _ = struct.unpack('2d', data)
                        LABEL_DETECTION_VALUE[port] = detected
                    else:
                        break
        except ConnectionRefusedError:
            print(f"[x] Connessione rifiutata alla porta {port}. Ritentativo in 5 secondi.")
        except socket.error as e:
            print(f"[x] Errore socket sulla porta {port}: {e}")
        time.sleep(5)

def server_thread(port):
    global DETECTED, terminate_flag, SENDER_FLAG_1
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', port))
            s.listen()
            print(f"\n[INFO] Server listening on port {port}")
            conn, addr = s.accept()
            with conn:
                print(f"[INFO] Connected to RobotVision through port {port}")
                while not terminate_flag:
                    data = conn.recv(1024)
                    if data:
                        deserialized_data = pickle.loads(data)
                        DETECTED = deserialized_data
                        SENDER_FLAG_1 = not SENDER_FLAG_1
                    else:
                        break
    except socket.error as e:
        print(f"[x] Errore socket sul server della porta {port}: {e}")

def sendInstruction(client_socket, instruction):
    global SENDER_FLAG_2
    try:
        print("[*] Sending instruction")
        client_socket.sendall(instruction.encode('utf-8'))
        data = client_socket.recv(1024)
        SENDER_FLAG_2 = not SENDER_FLAG_2
        print(f"[°] Server response: {data.decode('utf-8')}")
    except socket.error as e:
        print(f"[x] Errore durante l'invio dell'istruzione: {e}")

def send_thread(ip, port):
    global terminate_flag, SENDER_FLAG_1, SENDER_FLAG_2
    while not terminate_flag:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((ip, port))
                while not terminate_flag:
                    SEARCH_DICT = {PORT2LABEL[key]: value for key, value in LABEL_DETECTION_VALUE.items() if key in PORT2LABEL}
                    keys_above_threshold = {key: value for key, value in SEARCH_DICT.items() if value > CONFIDENCE_THRESHOLD}

                    if len(keys_above_threshold) > 0 and (SENDER_FLAG_1 and SENDER_FLAG_2):
                        max_key = max(keys_above_threshold.items(), key=lambda x: x[1])[0]
                        associatedFreq = LABEL2FREQ[max_key]
                        target = findDetectedObject(DETECTED, associatedFreq)

                        if target:
                            instr = f"GRAB|{target.position[0]},{target.position[1]},{target.position[2]}"
                            sendInstruction(s, instr)
                            SENDER_FLAG_1 = not SENDER_FLAG_1
                            SENDER_FLAG_2 = not SENDER_FLAG_2
                    
                    #threading.Event().wait(5)

        except ConnectionRefusedError:
            print(f"[x] Connessione rifiutata all'indirizzo {ip}:{port}. Ritentativo in 5 secondi.")
        except socket.error as e:
            print(f"[x] Errore socket sulla porta {port}: {e}")
        time.sleep(5)

if __name__ == "__main__":
    os.system("cls")

    parser = argparse.ArgumentParser(description="Tool di rilevamento oggetti con YOLOv8")
    parser.add_argument('--ip', type=str, default="localhost", help="IP of the ROS Machine")
    args = parser.parse_args()
    
    # Avvia i thread client per ricevere i messaggi da OpenViBE
    threading.Thread(target=client_thread, args=(OPENVIBE_PORTS[0],), daemon=True).start()
    threading.Thread(target=client_thread, args=(OPENVIBE_PORTS[1],), daemon=True).start()
    threading.Thread(target=client_thread, args=(OPENVIBE_PORTS[2],), daemon=True).start()

    # Avvia il thread server per ricevere i messaggi da RobotVision
    threading.Thread(target=server_thread, args=(ROBOTVISION_PORT,), daemon=True).start()

    # Avvia il thread per inviare messaggi a un server specifico
    ip_ROS = args.ip
    threading.Thread(target=send_thread, args=(ip_ROS, ROS_PORT,), daemon=True).start()

    # Mantieni il programma attivo finché non viene premuto Ctrl+C
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            signal_handler(None, None)
