import cv2
import pyrealsense2 as rs
import numpy as np
import argparse
import time

import pupil_apriltags as ap

import os
import sys

from ultralytics import YOLO

import socket
import pickle

import pygame

class DetectedObject:
    
    def __init__(self, label, freq, position):
        self.label = label
        self.freq = freq
        self.position = position

DETECTED = []

COLLECTOR_PORT = 5681

MAX_OBJECTS = None
EXPANSION = 50

# Carica il modello YOLOv8
model = YOLO("yoloModel/yolov8n.pt")  # Usa il modello YOLOv8 nano

# Apriltag detector
aprilDetector = ap.Detector(families="tag25h9")
markerSize = 0.08 # meters - 6.1 cm
markerCorrection = np.array([0, 0.00, 0, 0], dtype=np.float32)

# Classi escluse
exclude_ids = [0, 56, 60, 62, 63, 72] # Include person class

matRobot2Marker = np.array([
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, -1]
], dtype=np.float32)

xRot = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])

tvecRobot2Marker = np.array([0.16, -0.11, 0.03], dtype=np.float32)

def get_camera():
    try:
        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
        config.enable_stream(rs.stream.depth, 1280, 720, rs.format.z16, 30)
        pipeline.start(config)
        print("Telecamera Intel RealSense connessa\n")
        return 'realsense', pipeline
    except Exception as e:
        print(f"Errore con RealSense: {e}. Utilizzo della webcam del laptop.\n")
    
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise Exception("Impossibile aprire la webcam del laptop")
    print("Webcam del laptop connessa")
    return 'webcam', cap

def release_camera(camera_type, camera):
    if camera_type == 'realsense':
        camera.stop()
    elif camera_type == 'webcam':
        camera.release()

def compute_iou(box1, box2):
    """Calcola l'Intersection over Union (IoU) tra due bounding box."""
    x1_max = max(box1[0], box2[0]) - EXPANSION
    y1_max = max(box1[1], box2[1]) 
    x2_min = min(box1[2], box2[2]) + EXPANSION
    y2_min = min(box1[3], box2[3])

    intersection_width = max(0, x2_min - x1_max)
    intersection_height = max(0, y2_min - y1_max)
    intersection_area = intersection_width * intersection_height

    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])

    union_area = box1_area + box2_area - intersection_area

    if union_area == 0:
        return 0.0

    return intersection_area / union_area

def get_object_position(box, camera_type, depth_frame, color_frame):

    x1, y1, x2, y2 = map(int, box)
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2

    if camera_type == 'realsense' and depth_frame is not None:
        
        depth_in_m = depth_frame.get_distance(center_x, center_y)
        rgbIntrinsics = color_frame.profile.as_video_stream_profile().intrinsics

        center_x_in_m = (center_x - rgbIntrinsics.ppx) / rgbIntrinsics.fx * depth_in_m
        center_y_in_m = (center_y - rgbIntrinsics.ppy) / rgbIntrinsics.fy * depth_in_m

        debugDepth = np.asanyarray(depth_frame.get_data())
        debugDepth = cv2.convertScaleAbs(debugDepth, alpha=0.03)
        depth_colormap = cv2.applyColorMap(debugDepth, cv2.COLORMAP_JET)

        return (center_x_in_m, center_y_in_m, depth_in_m)

    else:
        return (center_x, center_y, 0.0)
    
def remove_overlapping_boxes(results, camera_type, depth_frame, color_frame, exclude_labels=exclude_ids, max_objects=None):
    filtered_boxes = []
    filtered_scores_labels = []
    object_positions = []

    # Assicurati che exclude_labels sia una lista
    if exclude_labels is None:
        exclude_labels = []

    # Estrai le box, confidenze e etichette
    boxes = np.array([box.xyxy[0].tolist() for box in results[0].boxes])
    confidences = np.array([box.conf.item() for box in results[0].boxes])
    labels = np.array([model.names[int(box.cls)] for box in results[0].boxes])

    # Filtra le bounding box
    for box, confidence, label in zip(boxes, confidences, labels):
        if confidence > 0.5 and label not in exclude_labels:
            position = get_object_position(box, camera_type, depth_frame, color_frame)
            filtered_boxes.append(box)
            filtered_scores_labels.append((confidence, label))  # (confidenza, etichetta)
            object_positions.append(position)

    # Verifica le sovrapposizioni e mantieni solo le più vicine
    non_overlapping_boxes = []
    non_overlapping_scores_labels = []
    non_overlapping_positions = []

    for i, box1 in enumerate(filtered_boxes):
        overlap = False
        for j, box2 in enumerate(filtered_boxes):
            if i != j:
                # Calcola l'area di sovrapposizione
                if (box1[0] < box2[2] and box1[2] > box2[0] and
                    box1[1] < box2[3] and box1[3] > box2[1]):
                    overlap = True
                    # Confronta le posizioni z per decidere quale mantenere
                    if object_positions[i][2] < object_positions[j][2]:  # Mantieni box1
                        break
                    else:  # Mantieni box2
                        continue
        if not overlap:
            non_overlapping_boxes.append(box1)
            non_overlapping_scores_labels.append(filtered_scores_labels[i])
            non_overlapping_positions.append(object_positions[i])

    # Costruzione del risultato finale
    filtered_results = results[0]  # Crea un nuovo oggetto Results basato sul primo risultato
    filtered_results.boxes = []  # Inizializza le box filtrate

    # Limita il numero di bounding box se max_objects è specificato
    limit = max_objects if max_objects is not None else len(non_overlapping_boxes)

    for box, (score, label) in zip(non_overlapping_boxes[:limit], non_overlapping_scores_labels[:limit]):
        # Aggiungi la box filtrata a filtered_results
        filtered_results.boxes.append({
            "xyxy": box,
            "conf": score,
            "cls": label
        })

    return (filtered_results.plot(), filtered_results, non_overlapping_boxes[:limit], non_overlapping_scores_labels[:limit], non_overlapping_positions[:limit])

def perform_detection(frame, include_persons, cameraType, depth_frame, color_frame):
    results = model(frame)

    if not results or len(results) == 0:
        print("Nessun risultato dalla rilevazione.")
        return frame, results, [], [], []

    if not include_persons:
        filtered_boxes = [box for box in results[0].boxes if int(box.cls) not in exclude_ids]
        results[0].boxes = filtered_boxes

    boxes = [box.xyxy[0].tolist() for box in results[0].boxes]
    scores = [box.conf.item() for box in results[0].boxes]
    labels = [model.names[int(box.cls)] for box in results[0].boxes]
    
    boxes = boxes[:MAX_OBJECTS]
    scores = scores[:MAX_OBJECTS]
    labels = labels[:MAX_OBJECTS]

    boxes_with_confidence = [(box, score, label) for box, score, label in zip(boxes, scores, labels) if score >= 0.5]

    if not boxes_with_confidence:
        return frame, results, [], [], []

    keep_boxes = []
    box_map = {}
    for i in range(len(boxes_with_confidence)):
        keep = True
        for j in range(len(boxes_with_confidence)):
            if i != j and compute_iou(boxes_with_confidence[i][0], boxes_with_confidence[j][0]) > 0:
                objpos1 = get_object_position(boxes_with_confidence[i][0], cameraType, depth_frame, color_frame)
                objpos2 = get_object_position(boxes_with_confidence[j][0], cameraType, depth_frame, color_frame)
                if objpos1 > objpos2:
                    keep = False
                    break
        if keep:
            box_map[tuple(boxes_with_confidence[i][0])] = (boxes_with_confidence[i][1], boxes_with_confidence[i][2])

    filtered_boxes = list(box_map.keys())
    filtered_scores_labels = list(box_map.values())

    if len(filtered_boxes) > args.max_objects:
        filtered_boxes = filtered_boxes[:args.max_objects]

    filtered_results = results[0]
    filtered_results.boxes = [box for box in results[0].boxes if box.xyxy[0].tolist() in filtered_boxes]

    object_position = []
    if len(filtered_boxes) > 0:
        for box in filtered_boxes:
            objectPositionToCamera = get_object_position(box, cameraType, depth_frame, color_frame)
            object_position.append(objectPositionToCamera)

    return filtered_results.plot(), filtered_results, filtered_boxes, filtered_scores_labels, object_position

def compute_frequencies(screen_freq, max_objects):
    if max_objects == 2 and screen_freq == 144:
        return [16, 18]
    elif max_objects == 3 and screen_freq == 144:
        return [16, 18, 24]
    else:
        return [freq for freq in range(1, screen_freq + 1) if screen_freq % freq == 0]

def addAxes(frame, camera_matrix, dist_coeffs, rvec, tvec, axis_length=0.05):
    
    axis_points = np.float32([[axis_length, 0, 0], [0, axis_length, 0], [0, 0, axis_length], [0, 0, 0]]).reshape(-1, 3)

    
    image_points, _ = cv2.projectPoints(axis_points, rvec, tvec, camera_matrix, dist_coeffs)

    
    image_points = np.int32(image_points).reshape(-1, 2)

    
    frame = cv2.line(frame, tuple(image_points[3]), tuple(image_points[0]), (0, 0, 255), 3)  # Asse X in rosso
    frame = cv2.line(frame, tuple(image_points[3]), tuple(image_points[1]), (0, 255, 0), 3)  # Asse Y in verde
    frame = cv2.line(frame, tuple(image_points[3]), tuple(image_points[2]), (255, 0, 0), 3)  # Asse Z in blu

    return frame

def detectApriltag(frameRealsense, detector):

    intrinsics = frameRealsense.profile.as_video_stream_profile().intrinsics

    ppx = intrinsics.ppx
    ppy = intrinsics.ppy
    fx = intrinsics.fx
    fy = intrinsics.fy

    cameraParams = (fx, fy, ppx, ppy)
    cameraMatrix = np.array([
        [fx, 0, ppx],
        [0, fy, ppy],
        [0, 0, 1]
    ], dtype=np.float32)

    distortionCoeffs = np.asanyarray(intrinsics.coeffs, dtype=np.float32)

    frame = np.asanyarray(frameRealsense.get_data())

    copyFrame = frame.copy()
    bgFrame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    detected = detector.detect(bgFrame, estimate_tag_pose=True, camera_params=cameraParams, tag_size=markerSize)
    
    if len(detected) > 0:
        for detection in detected:

            tvec = np.asanyarray(detection.pose_t)

            rMat = np.asanyarray(detection.pose_R)
            rvec, _ = cv2.Rodrigues(rMat)

            corners = detection.corners.astype(np.int32)
            copyFrame = cv2.circle(copyFrame, (int(detection.center[0]), int(detection.center[1])), 5, (0, 0, 255), -1)
            copyFrame = cv2.polylines(copyFrame, [corners], True, (0, 255, 0), thickness = 3)
            copyFrame = addAxes(copyFrame, cameraMatrix, distortionCoeffs, rvec, tvec)

            hmat = np.concatenate((rMat, tvec), axis=1)
            T_camera_to_marker = np.vstack((hmat, np.array([[0, 0, 0, 1]])))
            T_marker_to_camera = np.linalg.inv(T_camera_to_marker)
        
        return copyFrame, T_marker_to_camera
    else:
         return frame, None


if __name__ == "__main__":

    os.system("cls")

    parser = argparse.ArgumentParser(description="Tool di rilevamento oggetti con YOLOv8")
    parser.add_argument('--include-persons', action='store_true', help="Includi il rilevamento delle persone")
    parser.add_argument('--screen-freq', type=int, default=144, help="Frequenza di refresh del monitor")
    parser.add_argument('--max-objects', type=int, default=3, help="Numero massimo di oggetti rilevabili")
    args = parser.parse_args()

    camera_type, camera = get_camera()
    
    MAX_OBJECTS = args.max_objects

    detection_mode = False
    detection_frame = None

    frequencies = compute_frequencies(args.screen_freq, args.max_objects)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:

        CONNECTION_ENSTABLISHED = False
        MAX_TRY = 1
        for i in range(0,MAX_TRY):
            try:
                sock.connect(("localhost", COLLECTOR_PORT))
                CONNECTION_ENSTABLISHED = not CONNECTION_ENSTABLISHED
                print("\n[INFO] Online mode\n")
            except:
                print("[x] Retry in 5 seconds.")
                time.sleep(1)

        if not CONNECTION_ENSTABLISHED:
            print("\n[INFO] Offline mode\n")
        

        while True:
            if camera_type == 'realsense':
                frames = camera.wait_for_frames()

                align = rs.align(rs.stream.color)
                aligned_frames = align.process(frames)

                depth_frame = aligned_frames.get_depth_frame()
                color_frame = aligned_frames.get_color_frame()

                numpyColorFrame = np.asanyarray(color_frame.get_data())

                if not color_frame or not depth_frame:
                    print("Errore nel recupero dei frame della telecamera")
                    continue
                frame = np.asanyarray(color_frame.get_data())
            elif camera_type == 'webcam':
                ret, frame = camera.read()
                if not ret:
                    print("Impossibile catturare il frame dalla webcam")
                    break

            key = cv2.waitKey(1) & 0xFF
            if key == ord('s'):
                detection_mode = not detection_mode
                DETECTED = []
                if detection_mode:
                    print("Rilevazione oggetti in corso...")
                    detection_frame, results, filtered_boxes, filtered_scores_labels, object_position = perform_detection(frame, args.include_persons, camera_type, depth_frame, color_frame)
                    aprilFrame, hMat = detectApriltag(color_frame, aprilDetector)
                    cv2.imshow("April detector", aprilFrame)

                    if not filtered_boxes:
                        print("Nessun oggetto rilevato.")
                        detection_mode = False
                    else:
                        print("\n")
                        counter = 0
                        for idx, (freq, (score, label), position) in enumerate(zip(frequencies[:len(filtered_boxes)], filtered_scores_labels, object_position)):

                            x, y, z = position

                            if hMat is not None:
                                pMarker = np.dot(hMat, np.array([x, y, z, 1])) + markerCorrection
                                pRobot = np.dot(np.linalg.inv(xRot), np.array([pMarker[0], pMarker[1], pMarker[2]])) + tvecRobot2Marker
                                
                            else:
                                pMarker = np.array([0, 0, 0, 0])
                                pRobot = np.array([0, 0, 0, 0])

                            DETECTED.append(DetectedObject(label, freq, pRobot))
                            counter += 1
                            print(f"[LABEL]: {label} - [Freq]: {freq} Hz - [Posizione (Camera)]: ({x:.4f}, {y:.4f}, {z:.4f}) - [Posizione (Marker)]: ({pMarker[0]:.4f}, {pMarker[1]:.4f}, {pMarker[2]:.4f}) - [Posizione (Robot)]: ({pRobot[0]:.4f}, {pRobot[1]:.4f}, {pRobot[2]:.4f}) - [CONFIDENCE]: {filtered_scores_labels[idx][0]:.2f}")
                        print("\n")
                        if CONNECTION_ENSTABLISHED and hMat is not None:
                            serializedDetected = pickle.dumps(DETECTED)
                            sock.sendall(serializedDetected)
                            print("sended")
                        else:
                            print("Data not sended")

                else:
                    print("Acquisizione normale ripristinata.")

            if detection_mode:
                # Crea una copia del frame annotato per modificare solo l'effetto di lampeggio
                blink_frame = np.copy(detection_frame)
                blink_frame = cv2.cvtColor(blink_frame, cv2.COLOR_BGR2RGB)
                blink_frame = np.swapaxes(blink_frame, 0 , 1)
                
                periodBlink = 1/np.array(frequencies, dtype=np.float64)
                
                pygame.init()
                screen = pygame.display.set_mode((1280, 720))
                pygame.display.set_caption("Detection window")
                background_surface = pygame.surfarray.make_surface(blink_frame)
                clock = pygame.time.Clock()
                
                running = True
                
                last_toggle_times = [time.time()] * len(frequencies)
                is_visible = [True] * len(frequencies)  # Stato di visibilità per ciascun rettangolo
                
                while running:
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            running = False
                        elif event.type == pygame.KEYDOWN:
                            if event.key == pygame.K_s:  # Chiude la finestra se si preme 'S'
                                running = False
                            elif event.key == pygame.K_q:
                                pygame.quit()
                                
                    # Disegna lo sfondo
                    screen.blit(background_surface, (0, 0))
                    
                    dark_overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)  # La superficie ha lo stesso size dello schermo
                    dark_overlay.fill((0, 0, 0, 200))  # Nero con 50% di trasparenza (128 su 255)

                    # Disegna l'alone nero sopra l'immagine di sfondo
                    screen.blit(dark_overlay, (0, 0))
                    
                    current_time = time.time()
                    
                    # Gestisci il lampeggio dei rettangoli
                    for i, ((x_min, y_min, x_max, y_max), (score, label)) in enumerate(zip(filtered_boxes, filtered_scores_labels)):
                        elapsed_time = current_time - last_toggle_times[i]
                        
                        # Definisci l'espansione desiderata per lato (ad esempio, 10 pixel per lato)

                        # Espandi i bordi della bounding box
                        x_min = x_min - EXPANSION
      
                        x_max = x_max + EXPANSION
    

                        # Controlla se è ora di alternare la visibilità (accensione/spegnimento)
                        if elapsed_time >= periodBlink[i] / 2:
                            is_visible[i] = not is_visible[i]  # Alterna la visibilità
                            last_toggle_times[i] = current_time  # Resetta il tempo per il prossimo ciclo
                            
                        # Disegna il contorno verde fluo (sempre visibile)
                        outline_color = (0, 255, 0)  # Verde fluo
                        pygame.draw.rect(screen, outline_color, (x_min, y_min, x_max - x_min, y_max - y_min), 5)  # Spessore 2

                        # Disegna il rettangolo solo se è visibile
                        if is_visible[i]:
                            rect_color = (255, 255, 255)  # Colore bianco
                            pygame.draw.rect(screen, rect_color, (x_min, y_min, x_max - x_min, y_max - y_min), 0)
                            
                        # Aggiungi la frequenza sopra la bounding box (in verde fluo)
                        freq_text = f'{frequencies[i]:.0f} Hertz'  # Frequenza in Hertz
                        label_text = f'{label} {score:.2f}'  # Testo da visualizzare (label e score)
                        
                        font = pygame.font.SysFont(None, 24)  # Imposta il font e la dimensione
                        text_color = (0, 255, 0)  # Colore verde fluo

                        # Posizioni per il testo (sopra la bounding box)
                        freq_position = (x_min, y_min - 40)  # Posiziona la frequenza sopra la label
                        label_position = (x_min, y_min - 20)  # Posiziona la label appena sopra la bounding box

                        # Renderizza il testo e disegna sullo schermo
                        freq_surface = font.render(freq_text, True, text_color)
                        label_surface = font.render(label_text, True, text_color)
                        
                        screen.blit(freq_surface, freq_position)  # Disegna la frequenza
                        screen.blit(label_surface, label_position)  # Disegna la label e score

                    # Aggiorna il display
                    pygame.display.flip()

                    # Limita a 144 fps
                    clock.tick(144)
                
                pygame.quit()
                
                detection_mode = not detection_mode
                
            else:
                cv2.imshow('Video Feed', frame)

            if key == ord('q'):
                break
    finally:
        release_camera(camera_type, camera)
        cv2.destroyAllWindows()


    # 192.168.101.100
    # 192.168.101.10
    # JACO IP