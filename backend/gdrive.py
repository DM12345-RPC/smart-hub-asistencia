import os
import logging
import threading
import queue
import time
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import re

logger = logging.getLogger(__name__)

# Configuración por defecto
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "service_account.json")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "0AFbDLWIfNmO1Uk9PVA")

SCOPES = ["https://www.googleapis.com/auth/drive"]

_drive_service = None
_drive_lock = threading.RLock()

# Índice de fotos en memoria (O(1)) para evitar miles de llamadas lentas y colisiones SSL
_TOKEN_TO_FID = {}
_MARCAJE_MAP = {}
_MARCAJES_CATALOGO = []  # Tuplas (norm_fn, fid, d_str, tipo_m)
_EXACT_NAME_TO_FID = {}
_INDEX_CARGADO = False
_INDEX_LOCK = threading.Lock()
_download_semaphore = threading.BoundedSemaphore(2)

def _normalizar_txt(txt: str) -> str:
    if not txt:
        return ""
    txt = txt.lower()
    for a, b in [('á','a'), ('é','e'), ('í','i'), ('ó','o'), ('ú','u'), ('ñ','n')]:
        txt = txt.replace(a, b)
    return re.sub(r'[^a-z0-9]', '', txt)

# Cola de tareas de fondo para Google Drive (1 solo hilo worker serializado)
# Evita terminantemente errores [SSL] record layer failure, timeouts y colisiones entre hilos.
_drive_queue = queue.Queue()
_worker_started = False
_worker_start_lock = threading.Lock()
_db_sync_scheduled = False
_db_sync_lock = threading.Lock()


def obtener_servicio_drive():
    """
    Retorna el cliente de Google Drive autenticado con la cuenta de servicio.
    Si ocurrió un fallo de red o socket previo, se auto-recupera creando una sesión limpia.
    """
    global _drive_service
    with _drive_lock:
        if _drive_service is not None:
            return _drive_service

        creds = None

        # 1. Variable de entorno GDRIVE_SERVICE_ACCOUNT_JSON (Ideal para Render y repos públicos)
        env_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
        if env_json and env_json.strip():
            try:
                import json
                info = json.loads(env_json.strip())
                creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            except Exception as e:
                logger.error(f"Error cargando credenciales de variable GDRIVE_SERVICE_ACCOUNT_JSON: {e}")

        # 2. Secret File en Render (/etc/secrets/service_account.json o en la raíz)
        posibles_secret_files = [
            "/etc/secrets/service_account.json",
            os.path.join(BASE_DIR, "service_account.json")
        ]
        for s_file in posibles_secret_files:
            if not creds and os.path.exists(s_file):
                try:
                    creds = service_account.Credentials.from_service_account_file(s_file, scopes=SCOPES)
                    break
                except Exception as e:
                    logger.error(f"Error cargando credenciales de {s_file}: {e}")

        # 3. Archivo local backend/service_account.json (para pruebas locales)
        if not creds and os.path.exists(CREDENTIALS_FILE):
            try:
                creds = service_account.Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
            except Exception as e:
                logger.error(f"Error cargando credenciales locales de {CREDENTIALS_FILE}: {e}")

        if not creds:
            logger.warning("No se encontraron credenciales válidas de Google Drive.")
            return None

        try:
            _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
            return _drive_service
        except Exception as e:
            logger.error(f"Error autenticando con Google Drive API: {e}")
            return None


def _resetear_servicio():
    """Descarta el socket actual para forzar reconexión SSL limpia."""
    global _drive_service
    with _drive_lock:
        _drive_service = None


def subir_archivo_drive(ruta_local: str, nombre_remoto: str = None, carpeta_id: str = None, mimetype: str = None) -> dict:
    """
    Sube un archivo a Google Drive (compatible con Unidades Compartidas / Shared Drives).
    Retorna un diccionario con el status y el file_id o el error.
    Protegido con cerrojo para garantizar exclusión mutua en llamadas de API.
    """
    if not os.path.exists(ruta_local):
        return {"status": "error", "error": f"El archivo local no existe: {ruta_local}"}

    with _drive_lock:
        service = obtener_servicio_drive()
        if not service:
            return {"status": "error", "error": "No se pudo conectar con el servicio de Google Drive"}

        target_folder = carpeta_id or GDRIVE_FOLDER_ID
        if not target_folder:
            return {"status": "error", "error": "No se configuró el ID de carpeta de Google Drive"}

        nombre_archivo = nombre_remoto or os.path.basename(ruta_local)

        if not mimetype:
            if ruta_local.lower().endswith(".jpg") or ruta_local.lower().endswith(".jpeg"):
                mimetype = "image/jpeg"
            elif ruta_local.lower().endswith(".png"):
                mimetype = "image/png"
            elif ruta_local.lower().endswith(".csv"):
                mimetype = "text/csv"
            elif ruta_local.lower().endswith(".db") or ruta_local.lower().endswith(".sqlite"):
                mimetype = "application/x-sqlite3"
            else:
                mimetype = "application/octet-stream"

        try:
            file_metadata = {
                "name": nombre_archivo,
                "parents": [target_folder]
            }
            media = MediaFileUpload(ruta_local, mimetype=mimetype, resumable=False)

            archivo = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, webViewLink",
                supportsAllDrives=True
            ).execute()

            f_id = archivo.get("id")
            f_name = archivo.get("name")
            if f_id and f_name:
                with _INDEX_LOCK:
                    _EXACT_NAME_TO_FID[f_name] = f_id
                    base_clean = f_name.replace(".jpg", "").replace(".png", "")
                    for part in base_clean.split("_"):
                        if len(part) >= 6:
                            _TOKEN_TO_FID[part.lower()] = f_id
                    if not f_name.startswith("PERFIL_") and not f_name.endswith(".db"):
                        norm_fn = _normalizar_txt(base_clean)
                        d_str = base_clean[:10] if (len(base_clean) >= 10 and base_clean[4] == '-' and base_clean[7] == '-') else ""
                        tipo_m = "ENTRADA" if "ENTRADA" in f_name.upper() else ("SALIDA" if "SALIDA" in f_name.upper() else "")
                        _MARCAJES_CATALOGO.append((norm_fn, f_id, d_str, tipo_m))

            logger.info(f"✅ Archivo subido exitosamente a Google Drive: {nombre_archivo} (ID: {f_id})")
            return {
                "status": "ok",
                "file_id": f_id,
                "name": f_name,
                "link": archivo.get("webViewLink")
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ Error subiendo {nombre_archivo} a Google Drive: {error_msg}")
            # Si hubo error de capa SSL o timeout, resetear para reconectar limpio en el siguiente intento
            global _drive_service
            _drive_service = None
            return {"status": "error", "error": error_msg}


def sincronizar_db_a_drive_master(carpeta_id: str = None) -> dict:
    """
    Sube o actualiza 'asistencia_master.db' en Google Drive.
    Si ya existe un archivo con ese nombre en la carpeta, lo actualiza en su lugar (sin duplicar).
    Protegido con cerrojo para evitar colisiones de hilos.
    """
    db_path = os.path.join(BASE_DIR, "data", "asistencia.db")
    if not os.path.exists(db_path):
        return {"status": "error", "error": "Base de datos no encontrada"}

    with _drive_lock:
        service = obtener_servicio_drive()
        if not service:
            return {"status": "error", "error": "No hay conexión con Google Drive"}

        target_folder = carpeta_id or GDRIVE_FOLDER_ID
        if not target_folder:
            return {"status": "error", "error": "No hay ID de carpeta de Drive"}

        try:
            q = f"'{target_folder}' in parents and name = 'asistencia_master.db' and trashed = false"
            res = service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            files = res.get('files', []) if isinstance(res, dict) else []
            media = MediaFileUpload(db_path, mimetype="application/x-sqlite3", resumable=False)

            if files:
                file_id = files[0]['id']
                archivo = service.files().update(
                    fileId=file_id,
                    media_body=media,
                    supportsAllDrives=True
                ).execute()
                logger.info(f"✅ 'asistencia_master.db' actualizado en Drive (ID: {file_id})")
                return {"status": "ok", "file_id": file_id, "name": "asistencia_master.db", "action": "update"}
            else:
                meta = {"name": "asistencia_master.db", "parents": [target_folder]}
                archivo = service.files().create(
                    body=meta,
                    media_body=media,
                    supportsAllDrives=True,
                    fields="id, name"
                ).execute()
                logger.info(f"✅ 'asistencia_master.db' creado en Drive (ID: {archivo.get('id')})")
                return {"status": "ok", "file_id": archivo.get('id'), "name": "asistencia_master.db", "action": "create"}
        except Exception as e:
            logger.error(f"❌ Error sincronizando asistencia_master.db a Drive: {e}")
            global _drive_service
            _drive_service = None
            return {"status": "error", "error": str(e)}


def cargar_indice_drive(carpeta_id: str = None) -> int:
    """
    Carga el catálogo de fotos y archivos de Google Drive en memoria (1 sola llamada atómica).
    Indexa fotos de perfil (por token y trabajador) y fotos de marcaje (por fecha, trabajador y tipo).
    Permite responder descargas en <0.001s y evita bloqueos de hilos en Render.
    """
    global _INDEX_CARGADO
    with _INDEX_LOCK:
        if _INDEX_CARGADO:
            return len(_EXACT_NAME_TO_FID)

        service = obtener_servicio_drive()
        if not service:
            return 0

        target_folder = carpeta_id or GDRIVE_FOLDER_ID
        try:
            files = []
            page_token = None
            with _drive_lock:
                q = f"'{target_folder}' in parents and trashed = false"
                while True:
                    res = service.files().list(
                        q=q,
                        pageSize=1000,
                        pageToken=page_token,
                        fields="nextPageToken, files(id, name)",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True
                    ).execute()
                    f_list = res.get('files', []) if isinstance(res, dict) else []
                    files.extend(f_list)
                    page_token = res.get('nextPageToken')
                    if not page_token:
                        break

            for f in files:
                fn = f.get('name', '')
                fid = f.get('id')
                if not fn or not fid:
                    continue
                _EXACT_NAME_TO_FID[fn] = fid
                clean = fn.replace(".jpg", "").replace(".png", "")

                for part in clean.split("_"):
                    if len(part) >= 6:
                        _TOKEN_TO_FID[part.lower()] = fid

                if not fn.startswith("PERFIL_") and not fn.endswith(".db"):
                    norm_fn = _normalizar_txt(clean)
                    d_str = clean[:10] if (len(clean) >= 10 and clean[4] == '-' and clean[7] == '-') else ""
                    tipo_m = "ENTRADA" if "ENTRADA" in fn.upper() else ("SALIDA" if "SALIDA" in fn.upper() else "")
                    _MARCAJES_CATALOGO.append((norm_fn, fid, d_str, tipo_m))

            _INDEX_CARGADO = True
            logger.info(f"✅ Catálogo de fotos Google Drive precargado: {len(files)} archivos indexados en memoria.")
            return len(files)
        except Exception as e:
            logger.warning(f"Aviso al precargar catálogo de Drive en memoria: {e}")
            return 0


def _ensure_worker():
    """Garantiza que el hilo único de subidas a Google Drive esté corriendo de forma segura."""
    global _worker_started
    with _worker_start_lock:
        if not _worker_started:
            t = threading.Thread(target=_drive_worker_loop, daemon=True, name="DriveSyncWorker")
            t.start()
            _worker_started = True


def _drive_worker_loop():
    """Worker en segundo plano que procesa subidas y respaldos uno por uno (serializado)."""
    global _db_sync_scheduled
    while True:
        try:
            item = _drive_queue.get()
            if item is None:
                break

            task_type, payload = item

            if task_type == "upload_file":
                ruta_local, nombre_remoto, carpeta_id, mimetype = payload
                for intento in range(2):
                    res = subir_archivo_drive(ruta_local, nombre_remoto, carpeta_id, mimetype)
                    if res.get("status") == "ok":
                        break
                    time.sleep(1.5)

            elif task_type == "sync_db":
                carpeta_id = payload
                # Pequeña pausa de 2s para debounce (acumular ráfagas de deletes/inserts en una sola subida)
                time.sleep(2.0)
                with _db_sync_lock:
                    _db_sync_scheduled = False

                for intento in range(2):
                    res = sincronizar_db_a_drive_master(carpeta_id)
                    if res.get("status") == "ok":
                        break
                    time.sleep(1.5)

        except Exception as e:
            logger.error(f"Error inesperado en loop worker de Google Drive: {e}")
        finally:
            _drive_queue.task_done()


def subir_foto_async(ruta_local: str, nombre_remoto: str, carpeta_id: str = None):
    """Encola la subida de foto al worker serializado sin bloquear ni colisionar sockets."""
    _ensure_worker()
    _drive_queue.put(("upload_file", (ruta_local, nombre_remoto, carpeta_id, None)))


def sincronizar_db_a_drive_master_async(carpeta_id: str = None):
    """Encola la sincronización de la DB maestra con debounce para evitar saturación de red."""
    global _db_sync_scheduled
    _ensure_worker()
    with _db_sync_lock:
        if not _db_sync_scheduled:
            _db_sync_scheduled = True
            _drive_queue.put(("sync_db", carpeta_id))


def realizar_respaldo_completo(carpeta_id: str = None) -> dict:
    """Sube una copia de la base de datos sqlite y los reportes a Google Drive."""
    db_path = os.path.join(BASE_DIR, "data", "asistencia.db")
    if not os.path.exists(db_path):
        return {"status": "error", "error": "Base de datos no encontrada"}

    fecha_hora = datetime.now().strftime("%Y-%m-%d_%H%M")
    nombre_db_backup = f"asistencia_backup_{fecha_hora}.db"
    return subir_archivo_drive(db_path, nombre_remoto=nombre_db_backup, carpeta_id=carpeta_id)


def restaurar_db_desde_drive_master(carpeta_id: str = None) -> bool:
    """
    Descarga 'asistencia_master.db' desde Google Drive al archivo local data/asistencia.db
    si la base de datos local está vacía o no existe.
    """
    db_path = os.path.join(BASE_DIR, "data", "asistencia.db")
    with _drive_lock:
        service = obtener_servicio_drive()
        if not service:
            logger.warning("No se pudo conectar a Google Drive para restaurar base de datos.")
            return False

        target_folder = carpeta_id or GDRIVE_FOLDER_ID
        try:
            q = f"'{target_folder}' in parents and name = 'asistencia_master.db' and trashed = false"
            res = service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            files = res.get('files', []) if isinstance(res, dict) else []
            if not files:
                logger.info("No se encontró 'asistencia_master.db' en Google Drive para restaurar.")
                return False

            from googleapiclient.http import MediaIoBaseDownload
            file_id = files[0]['id']
            req = service.files().get_media(fileId=file_id, supportsAllDrives=True)
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

            temp_path = db_path + ".temp"
            with open(temp_path, "wb") as f:
                downloader = MediaIoBaseDownload(f, req)
                done = False
                while not done:
                    _, done = downloader.next_chunk()

            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 1024:
                try:
                    os.replace(temp_path, db_path)
                    logger.info(f"✅ Base de datos restaurada con éxito desde Google Drive ('asistencia_master.db', ID: {file_id}).")
                    return True
                except Exception as e:
                    logger.error(f"Error reemplazando archivo de base de datos con versión de Drive: {e}")
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    return False
            else:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                return False
        except Exception as e:
            logger.error(f"❌ Error restaurando base de datos desde Google Drive: {e}")
            global _drive_service
            _drive_service = None
            return False


def descargar_foto_desde_drive(nombre_archivo: str, destino_local: str, carpeta_id: str = None) -> bool:
    """
    Busca y descarga una foto (perfil o captura) desde Google Drive al disco local de Render.
    Usa el catálogo en memoria precargado (O(1)) para resolución instantánea sin esperas ni loops.
    Si el archivo no existe en el catálogo de Drive, retorna False en 0.0001s evitando saturar el servidor.
    """
    if not nombre_archivo:
        return False

    # 1. Asegurar que el catálogo de Drive esté cargado en memoria
    if not _INDEX_CARGADO:
        cargar_indice_drive(carpeta_id)

    base_nom = os.path.basename(nombre_archivo)
    limpio = base_nom.replace(".jpg", "").replace(".png", "")
    token = (limpio.split("_")[-1] if "_" in limpio else limpio).lower()

    # 2. Búsqueda instantánea en catálogo de memoria (0.00001s)
    file_id = _EXACT_NAME_TO_FID.get(base_nom) or _TOKEN_TO_FID.get(token)

    # 3. Si es marcaje de asistencia (punch_ o captura_), resolver mediante cruce de fecha y trabajador
    if not file_id and ("punch_" in base_nom or "captura_" in base_nom):
        try:
            import sqlite3
            db_p = os.path.join(BASE_DIR, "data", "asistencia.db")
            if os.path.exists(db_p):
                conn = sqlite3.connect(db_p)
                c = conn.cursor()
                c.execute("SELECT empleado_nombre, tipo, timestamp_local FROM marcajes WHERE foto_path LIKE ? LIMIT 1", (f"%{base_nom}%",))
                row = c.fetchone()
                conn.close()
                if row:
                    emp_norm = _normalizar_txt(row[0])
                    tipo_m = str(row[1]).upper().strip()
                    d_str = str(row[2])[:10] if row[2] else ""
                    # 1. Búsqueda exacta por fecha, tipo y nombre de empleado
                    for norm_fn, m_fid, m_date, m_tipo in _MARCAJES_CATALOGO:
                        if m_date == d_str and m_tipo == tipo_m and emp_norm in norm_fn:
                            file_id = m_fid
                            break
                    # 2. Búsqueda de respaldo por tipo y nombre
                    if not file_id:
                        for norm_fn, m_fid, m_date, m_tipo in _MARCAJES_CATALOGO:
                            if m_tipo == tipo_m and emp_norm in norm_fn:
                                file_id = m_fid
                                break
        except Exception as e_sql:
            logger.warning(f"Error resolviendo marcaje en SQLite: {e_sql}")

    # Si NO está en el catálogo de Google Drive, salir inmediatamente sin bloquear
    if not file_id:
        return False

    service = obtener_servicio_drive()
    if not service:
        return False

    from googleapiclient.http import MediaIoBaseDownload
    try:
        with _download_semaphore:
            with _drive_lock:
                req = service.files().get_media(fileId=file_id, supportsAllDrives=True)
                dir_dest = os.path.dirname(destino_local)
                if dir_dest:
                    os.makedirs(dir_dest, exist_ok=True)
                temp_dest = destino_local + ".tmp"
                with open(temp_dest, "wb") as f:
                    downloader = MediaIoBaseDownload(f, req)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()

                if os.path.exists(temp_dest) and os.path.getsize(temp_dest) > 100:
                    os.replace(temp_dest, destino_local)
                    logger.info(f"✅ Foto '{base_nom}' descargada desde Drive (ID: {file_id})")
                    return True
                else:
                    if os.path.exists(temp_dest):
                        try:
                            os.remove(temp_dest)
                        except Exception:
                            pass
                    return False
    except Exception as e:
        logger.warning(f"Aviso descargando foto '{base_nom}' de Drive: {e}")
        return False

