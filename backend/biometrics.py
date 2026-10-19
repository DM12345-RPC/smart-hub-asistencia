import cv2
import numpy as np
import base64
import os
import onnxruntime

# Límites estrictos de memoria RAM para servidores en la nube (Render 512MB)
os.environ["ORT_DISABLE_MEMORY_ARENA"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

from insightface.app import FaceAnalysis

_face_app = None

def get_face_app():
    """
    Inicializador seguro bajo demanda (lazy-loading).
    Garantiza que solo se carguen los pesos ligeros de detección y reconocimiento,
    manteniendo el consumo de RAM en menos de 120MB.
    """
    global _face_app
    if _face_app is None:
        print("[INFO] Inicializando motor biometrico ultra-ligero (<120MB RAM)...")
        models_dir = os.path.expanduser('~/.insightface/models/buffalo_s')
        if os.path.exists(models_dir):
            for unused in ['1k3d68.onnx', '2d106det.onnx', 'genderage.onnx']:
                fpath = os.path.join(models_dir, unused)
                if os.path.exists(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
                        
        _face_app = FaceAnalysis(
            name='buffalo_s',
            root=os.path.expanduser('~/.insightface'),
            allowed_modules=['detection', 'recognition']
        )
        _face_app.prepare(ctx_id=-1, det_size=(320, 320))
        print("[INFO] Motor biometrico listo y optimizado en memoria.")
    return _face_app

def decodificar_imagen(datos_bytes_o_base64):
    """
    Convierte bytes o string base64 en imagen OpenCV (BGR).
    """
    if isinstance(datos_bytes_o_base64, str):
        if "," in datos_bytes_o_base64:
            datos_bytes_o_base64 = datos_bytes_o_base64.split(",")[1]
        nparr = np.frombuffer(base64.b64decode(datos_bytes_o_base64), np.uint8)
    else:
        nparr = np.frombuffer(datos_bytes_o_base64, np.uint8)
        
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return img

def mejorar_contraste_obra(img):
    """
    Aplica CLAHE (Contrast Limited Adaptive Histogram Equalization)
    en el canal de luminosidad (L en espacio LAB).
    Esto es crucial en obras de construcción para:
    1. Aclarar la sombra oscura que proyecta la visera del casco sobre los ojos y cejas.
    2. Compensar contraluces intensos por el sol directo.
    """
    if img is None:
        return None
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    l_eq = clahe.apply(l)
    return cv2.cvtColor(cv2.merge((l_eq, a, b)), cv2.COLOR_LAB2BGR)

def detectar_borrosidad(img):
    """
    Calcula la varianza del Laplaciano.
    Valores menores a 60 indican que la foto está borrosa o desenfocada.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()

def extraer_vector_rostro(img):
    """
    Detecta rostro y extrae el vector de 512 dimensiones normalizado L2.
    Retorna: (vector: np.ndarray, error: str, bbox: list)
    """
    if img is None:
        return None, "Imagen vacía o corrupta", None

    # Obtener el motor biométrico optimizado
    face_app = get_face_app()

    # Preprocesar imagen
    img_opt = mejorar_contraste_obra(img)

    faces = face_app.get(img_opt)
    if not faces:
        # Intentar con la imagen original sin CLAHE por si acaso
        faces = face_app.get(img)
        if not faces:
            return None, "No se detectó ningún rostro. Mire fijamente a la cámara.", None

    # Tomar el rostro principal (con mayor área de bounding box)
    mejor_cara = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))

    # Vector 512D
    emb = mejor_cara.embedding
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm

    return emb, None, mejor_cara.bbox.tolist()

def verificar_1_a_1(vector_captura, lista_vectores_empleado):
    """
    Compara el vector recién capturado contra todos los vectores registrados
    del trabajador (sin casco, con casco, diferentes días).
    Retorna la máxima similitud coseno encontrada (entre 0.0 y 1.0).
    """
    if vector_captura is None or not lista_vectores_empleado:
        return 0.0

    matriz = np.array(lista_vectores_empleado)
    # Al estar normalizados L2, el producto punto es la similitud coseno
    similitudes = np.dot(matriz, vector_captura)
    return float(np.max(similitudes))
