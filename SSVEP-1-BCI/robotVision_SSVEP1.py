import cv2
import pyrealsense2 as rs
import numpy as np
import argparse
import time

import pupil_apriltags as ap
import xml.etree.ElementTree as ET

import os

from ultralytics import YOLO

import socket
import pickle


class DetectedObject:
    
    def __init__(self, label, freq, position):
        self.label = label
        self.freq = freq
        self.position = position

DETECTED = []

COLLECTOR_PORT = 5683

# Carica il modello YOLOv8
model = YOLO("yoloModel/yolov8n.pt")  # Usa il modello YOLOv8 nano

# Apriltag detector
aprilDetector = ap.Detector(families="tag25h9")
markerSize = 0.061 # meters - 6.1 cm
markerCorrection = np.array([0, 0.00, 0, 0], dtype=np.float32)

# Classi escluse
exclude_ids = [0, 56, 60, 62, 63, 72] # Include person class

matRobot2Marker = np.array([
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, -1]
], dtype=np.float32)

tvecRobot2Marker = np.array([0, 0, 0], dtype=np.float32)

def save_to_xml(objects, filename):
    root = ET.Element("DetectedObjects")
    
    for obj in objects:
        obj_elem = ET.SubElement(root, "DetectedObject")
        
        label_elem = ET.SubElement(obj_elem, "label")
        label_elem.text = obj.label
        
        freq_elem = ET.SubElement(obj_elem, "freq")
        freq_elem.text = str(obj.freq)
        
        position_elem = ET.SubElement(obj_elem, "position")
        position_elem.text = f"{obj.position[0]},{obj.position[1]},{obj.position[2]}"  # Salva la posizione come stringa

    # Scrivi il file XML
    with open(filename, 'wb') as file:
        tree = ET.ElementTree(root)
        tree.write(file)


def get_camera():
    try:
        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        pipeline.start(config)
        print("Telecamera Intel RealSense connessa")
        return 'realsense', pipeline
    except Exception as e:
        print(f"Errore con RealSense: {e}. Utilizzo della webcam del laptop.")
    
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

def compute_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)

    boxAArea = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
    boxBArea = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)
    unionArea = boxAArea + boxBArea - interArea

    iou = interArea / unionArea
    return iou

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
        depth_colormap = cv2.applyColorMap(cv2.convertScaleAbs(debugDepth, alpha=0.03), cv2.COLORMAP_JET)
        cv2.imshow("Depth map", depth_colormap)

        return (center_x_in_m, center_y_in_m, depth_in_m)

    else:
        return (center_x, center_y, 0.0)

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

    boxes_with_confidence = [(box, score, label) for box, score, label in zip(boxes, scores, labels) if score >= 0.5]

    if not boxes_with_confidence:
        return frame, results, [], [], []

    keep_boxes = []
    box_map = {}
    for i in range(len(boxes_with_confidence)):
        keep = True
        for j in range(len(boxes_with_confidence)):
            if i != j and compute_iou(boxes_with_confidence[i][0], boxes_with_confidence[j][0]) > 0.5:
                if boxes_with_confidence[i][1] < boxes_with_confidence[j][1]:
                    keep = False
                    break
        if keep:
            box_map[tuple(boxes_with_confidence[i][0])] = (boxes_with_confidence[i][1], boxes_with_confidence[i][2])

    filtered_boxes = list(box_map.keys())
    filtered_scores_labels = list(box_map.values())

    filtered_results = results[0]
    filtered_results.boxes = [box for box in results[0].boxes if box.xyxy[0].tolist() in filtered_boxes]

    object_position = []
    if len(filtered_boxes) > 0:
        for box in filtered_boxes:
            objectPositionToCamera = get_object_position(box, cameraType, depth_frame, color_frame)
            object_position.append(objectPositionToCamera)

    return filtered_results.plot(), filtered_results, filtered_boxes, filtered_scores_labels, object_position

def compute_frequencies(screen_freq, max_objects):
    if max_objects == 3 and screen_freq == 60:
        return [12, 15, 20]
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
    parser.add_argument('--screen-freq', type=int, default=60, help="Frequenza di refresh del monitor")
    parser.add_argument('--max-objects', type=int, default=3, help="Numero massimo di oggetti rilevabili")
    args = parser.parse_args()

    camera_type, camera = get_camera()

    detection_mode = False
    detection_frame = None

    frequencies = compute_frequencies(args.screen_freq, args.max_objects)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.connect(("localhost", COLLECTOR_PORT))

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
                                pRobot = np.dot(matRobot2Marker, np.array([pMarker[0], pMarker[1], pMarker[2]])) + tvecRobot2Marker
                            else:
                                pMarker = np.array([0, 0, 0, 0])
                                pRobot = np.array([0, 0, 0, 0])

                            DETECTED.append(DetectedObject(label, freq, pRobot))
                            counter += 1
                            print(f"[LABEL]: {label} - [Freq]: {freq} Hz - [Posizione (Camera)]: ({x:.4f}, {y:.4f}, {z:.4f}) - [Posizione (Marker)]: ({pMarker[0]:.4f}, {pMarker[1]:.4f}, {pMarker[2]:.4f}) - [Posizione (Robot)]: ({pRobot[0]:.4f}, {pRobot[1]:.4f}, {pRobot[2]:.4f}) - [CONFIDENCE]: {filtered_scores_labels[idx][0]:.2f}")
                        print("\n")

                        serializedDetected = pickle.dumps(DETECTED)
                        sock.sendall(serializedDetected)

                else:
                    print("Acquisizione normale ripristinata.")

            if detection_mode:
                current_time = time.time()
                blink_frame = np.copy(detection_frame)

                for idx, box in enumerate(filtered_boxes):
                    if idx >= len(frequencies):
                        break

                    blink_state = int((current_time * frequencies[idx]) % 2)  # Alterna tra 0 e 1 in base alla frequenza
                    x1, y1, x2, y2 = map(int, box)
                    if blink_state == 1:
                        # Riempi la bounding box con colore bianco
                        blink_frame[y1:y2, x1:x2] = [255, 255, 255]

                    cv2.rectangle(blink_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(blink_frame, f'{filtered_scores_labels[idx][1]} ({filtered_scores_labels[idx][0]:.2f})', 
                                (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    
                cv2.imshow('Video Feed', blink_frame)
            elif not detection_mode:
                cv2.imshow('Video Feed', frame)

            if key == ord('q'):
                break
    finally:
        release_camera(camera_type, camera)
        cv2.destroyAllWindows()
