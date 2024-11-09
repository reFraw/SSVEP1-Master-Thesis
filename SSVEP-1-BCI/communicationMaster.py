import socket
import threading
import pickle
import argparse
import signal
import sys
import os
import time
import numpy as np

TERMINATE_FLAG = False

ROS_PORT = 8080
OPENVIBE_PORT = 5680
ROBOTVISION_PORT = 5681

SAVE_ROBOTVISION_DATA = True
SAVE_OPENVIBE_DATA = False
READY_TO_SEND_ROS_COMMAND = False

DETECTED = None
PREDICTED_CLASS = None

class DetectedObject:
    
    def __init__(self, label, freq, position):
        self.label = label
        self.freq = freq
        self.position = position
        
def signal_handler(sig, frame):
    global TERMINATE_FLAG
    print("\n[INFO] Interruzione rilevata, chiusura delle connessioni...")
    TERMINATE_FLAG = True
    sys.exit(0)
    
def findDetectedObject(objects, label_to_find):
    for obj in objects:
        if obj.freq == label_to_find:
            return obj
    return None  

def readRobotVisionData(ip, port):
    global SAVE_ROBOTVISION_DATA
    global SAVE_OPENVIBE_DATA
    global DETECTED
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((ip, port))
        s.listen()
        print(f"\n[INFO] Listening for RobotVision on port {port}")
        
        while not TERMINATE_FLAG:  # Ciclo per mantenere il server in ascolto
            try:
                conn, addr = s.accept()
                with conn:
                    print(f"[INFO] Connected to RobotVision through port {port}")
                    while not TERMINATE_FLAG:
                        data = conn.recv(1024)
                        if data:
                            if SAVE_ROBOTVISION_DATA:
                                deserialized_data = pickle.loads(data)
                                DETECTED = deserialized_data
                                SAVE_ROBOTVISION_DATA = not SAVE_ROBOTVISION_DATA
                                SAVE_OPENVIBE_DATA = not SAVE_OPENVIBE_DATA
                        else:
                            print("[INFO] Client RobotVision disconnesso.")
                            break
            except socket.error as e:
                print(f"[ERROR] Errore di connessione: {e}")
            except pickle.UnpicklingError:
                print("[ERROR] Errore durante la deserializzazione dei dati RobotVision.")
                
def readOpenvibeData(ip, port):
    global SAVE_OPENVIBE_DATA
    global READY_TO_SEND_ROS_COMMAND
    global PREDICTED_CLASS
    
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((ip, port))
        s.listen()
        print(f"\n[INFO] Listening for OpenViBE on port {port}")
        
        while not TERMINATE_FLAG:  # Ciclo per mantenere il server in ascolto
            try:
                conn, addr = s.accept()
                with conn:
                    print(f"[INFO] Connected to OpenViBE through port {port}")
                    while not TERMINATE_FLAG:
                        data = conn.recv(1024)
                        if data:
                            decodedData = data.decode('utf-8')
                            if SAVE_OPENVIBE_DATA:
                                print(f"[INFO] DETECTED PEAK AT {decodedData} Hz")
                                PREDICTED_CLASS = decodedData
                                SAVE_OPENVIBE_DATA = not SAVE_OPENVIBE_DATA
                                READY_TO_SEND_ROS_COMMAND = not READY_TO_SEND_ROS_COMMAND
                        else:
                            print("[INFO] Client OpenViBE disconnesso.")
                            break
            except socket.error as e:
                print(f"[ERROR] Errore di connessione: {e}")
            except UnicodeDecodeError:
                print("[ERROR] Errore durante la decodifica dei dati OpenViBE.")
                
def sendInstruction(client_socket, instruction):
    global SENDER_FLAG_2
    try:
        print("[*] Sending instruction")
        client_socket.sendall(instruction.encode('utf-8'))
        data = client_socket.recv(1024)
        print(f"[°] Server response: {data.decode('utf-8')}")
    except socket.error as e:
        print(f"[x] Errore durante l'invio dell'istruzione: {e}")
                
def sendROSCommand(ip, port):
    global SAVE_ROBOTVISION_DATA
    global SAVE_OPENVIBE_DATA
    global READY_TO_SEND_ROS_COMMAND
    
    global DETECTED
    global PREDICTED_CLASS
    
    while not TERMINATE_FLAG:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((ip, port))
                
                while not TERMINATE_FLAG:
                    if READY_TO_SEND_ROS_COMMAND:
                        targetObject = findDetectedObject(DETECTED, int(PREDICTED_CLASS))
                        
                        if targetObject:
                            
                            zOffset = 0.06
                            
                            xTarget = targetObject.position[0]
                            yTarget = targetObject.position[1]
                            zTarget = targetObject.position[2] + zOffset
                            
                            xDestination = -0.32
                            yDestination = -0.33
                            zDestination = zTarget + zOffset
                            
                            targetPlaneDistance = np.sqrt(xTarget**2 + yTarget**2)
                            
                            # Verifico che non sia troppo lontano o troppo vicino alla base del manipolatore
                            targetOuterWorkspace = targetPlaneDistance > 0.6 and zTarget > 0.6
                            targetInnerWorkspace = targetPlaneDistance < 0.15
                            
                            if targetOuterWorkspace or targetInnerWorkspace:
                                print("[INFO] Invalid position for selected object. Try detection again.")
                            else:
                                instr = f"GRABREL|{xTarget},{yTarget},{zTarget}|{xDestination},{yDestination},{zDestination}"
                                sendInstruction(s, instr)
                        else:
                            print("[INFO] Associated object not here. Try detection again.")
                            
                        DETECTED = None
                        PREDICTED_CLASS = None
                        targetObject = None
                            
                        SAVE_ROBOTVISION_DATA = not SAVE_ROBOTVISION_DATA
                        READY_TO_SEND_ROS_COMMAND = not READY_TO_SEND_ROS_COMMAND
                
        except ConnectionRefusedError:
            print(f"\n[x] Connessione rifiutata all'indirizzo {ip}:{port}. Ritentativo in 5 secondi.")
            
        except socket.error as e:
            print(f"\n[x] Errore socket sulla porta {port}: {e}. Ritentativo in 5 secondi.")
        time.sleep(5)
                    
            
signal.signal(signal.SIGINT, signal_handler)

if __name__ == "__main__":
    
    os.system("cls")
    
    parser = argparse.ArgumentParser(description="Communication master.")
    parser.add_argument('--ip', '-ip', type=str, default="localhost", help="IP of the ROS Machine")
    args = parser.parse_args()
    
    # RobotVision Thread
    threading.Thread(target=readRobotVisionData, args=("localhost", ROBOTVISION_PORT,), daemon=True).start()
    
    # OpenViBE Thread
    threading.Thread(target=readOpenvibeData, args=("localhost", OPENVIBE_PORT,), daemon=True).start()
    
    # ROS Thread
    threading.Thread(target=sendROSCommand, args=(args.ip, ROS_PORT,), daemon=True).start()
    
    # Mantieni il programma attivo finché non viene premuto Ctrl+C
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            signal_handler(None, None)