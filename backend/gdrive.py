import os
import logging
import threading
import queue
import time
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

# Configuración por defecto
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "service_account.json")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "0AFbDLWIfNmO1Uk9PVA")

SCOPES = ["https://www.googleapis.com/auth/drive"]

_drive_service = None
_drive_lock = threading.RLock()

# Caché en memoria para IDs de fotos en Drive (Evita miles de peticiones lentas files().list)
_DRIVE_ID_CACHE = {}          # token / filename -> file_id
_DRIVE_MISSING_CACHE = set()  # tokens que definitivamente no existen en Drive
_drive_cache_lock = threading.Lock()
_drive_index_loaded = False

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
                with _drive_cache_lock:
                    _DRIVE_ID_CACHE[f_name] = f_id
                    base_clean = f_name.replace(".jpg", "").replace(".png", "")
                    tokens = base_clean.split("_")
                    if tokens:
                        _DRIVE_ID_CACHE[tokens[-1]] = f_id

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


def indexar_fotos_drive_en_memoria(carpeta_id: str = None):
    """
    Escanea la carpeta de Google Drive en segundo plano una sola vez al iniciar el servidor
    para indexar en memoria los file_id de todas las fotos existentes.
    Permite descargas en <200ms sin saturar Drive con peticiones files().list.
    """
    global _drive_index_loaded
    if _drive_index_loaded:
        return
    try:
        service = obtener_servicio_drive()
        if not service:
            return
        target_folder = carpeta_id or GDRIVE_FOLDER_ID
        page_token = None
        total = 0
        while True:
            res = service.files().list(
                q=f"'{target_folder}' in parents and trashed = false",
                pageSize=500,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                fields="nextPageToken, files(id, name)"
            ).execute()
            files = res.get('files', []) if isinstance(res, dict) else []
            with _drive_cache_lock:
                for f in files:
                    name = f.get('name', '')
                    fid = f.get('id')
                    if name and fid:
                        _DRIVE_ID_CACHE[name] = fid
                        base_clean = name.replace(".jpg", "").replace(".png", "")
                        tokens = base_clean.split("_")
                        if tokens:
                            _DRIVE_ID_CACHE[tokens[-1]] = fid
                        total += 1
            page_token = res.get('nextPageToken')
            if not page_token:
                break
        _drive_index_loaded = True
        logger.info(f"✅ Índice de fotos Google Drive cargado en memoria: {total} archivos listos para descarga ultra-rápida.")
    except Exception as e:
        logger.warning(f"Aviso al precargar fotos de Drive en memoria: {e}")


def _ensure_worker():
    """Garantiza que el hilo único de subidas a Google Drive esté corriendo y el índice precargado."""
    global _worker_started
    with _worker_start_lock:
        if not _worker_started:
            t = threading.Thread(target=_drive_worker_loop, daemon=True, name="DriveSyncWorker")
            t.start()
            _worker_started = True
            # Iniciar precarga de índice en segundo plano
            t_idx = threading.Thread(target=indexar_fotos_drive_en_memoria, daemon=True, name="DriveIndexWorker")
            t_idx.start()


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
    Busca y descarga una foto (perfil o captura) desde Google Drive al disco local
    cuando Render recrea el contenedor y el archivo físico no existe en disco.
    Optimizado con caché en memoria y descarga directa por file_id sin esperas secuenciales.
    """
    if not nombre_archivo:
        return False
    
    base_nom = os.path.basename(nombre_archivo)
    limpio = base_nom.replace(".jpg", "").replace(".png", "")
    token = limpio.split("_")[-1] if "_" in limpio else limpio
    if len(token) < 4:
        token = limpio

    # 1. Chequeo ultra-rápido en memoria: ¿Sabemos que no existe o ya tenemos su file_id?
    with _drive_cache_lock:
        if token in _DRIVE_MISSING_CACHE or base_nom in _DRIVE_MISSING_CACHE:
            return False
        cached_file_id = _DRIVE_ID_CACHE.get(base_nom) or _DRIVE_ID_CACHE.get(token)

    file_id = cached_file_id
    service = obtener_servicio_drive()
    if not service:
        return False

    target_folder = carpeta_id or GDRIVE_FOLDER_ID
    from googleapiclient.http import MediaIoBaseDownload

    # 2. Si no estaba en caché, buscar en Drive
    if not file_id:
        with _drive_lock:
            try:
                q = f"'{target_folder}' in parents and name contains '{token}' and trashed = false"
                res = service.files().list(
                    q=q, 
                    supportsAllDrives=True, 
                    includeItemsFromAllDrives=True, 
                    fields="files(id, name)",
                    pageSize=5
                ).execute()
                files = res.get('files', []) if isinstance(res, dict) else []

                # Si no por token directo, buscar en SQLite el nombre del trabajador o marcaje
                if not files:
                    try:
                        import sqlite3
                        db_p = os.path.join(BASE_DIR, "data", "asistencia.db")
                        if os.path.exists(db_p):
                            conn = sqlite3.connect(db_p)
                            c = conn.cursor()
                            c.execute("SELECT empleado_nombre, tipo FROM marcajes WHERE foto_path LIKE ? ORDER BY id DESC LIMIT 1", (f"%{base_nom}%",))
                            row_m = c.fetchone()
                            if row_m:
                                emp_clean = str(row_m[0]).replace(" ", "_").strip()
                                m_tipo = str(row_m[1]).strip()
                                q2 = f"'{target_folder}' in parents and name contains '{emp_clean}' and name contains '{m_tipo}' and trashed = false"
                                res2 = service.files().list(q=q2, supportsAllDrives=True, includeItemsFromAllDrives=True, fields="files(id, name)", pageSize=3).execute()
                                files = res2.get('files', []) if isinstance(res2, dict) else []
                                if not files:
                                    q2_alt = f"'{target_folder}' in parents and name contains '{emp_clean}' and trashed = false"
                                    res2_alt = service.files().list(q=q2_alt, supportsAllDrives=True, includeItemsFromAllDrives=True, fields="files(id, name)", pageSize=3).execute()
                                    files = res2_alt.get('files', []) if isinstance(res2_alt, dict) else []

                            # Buscar en empleados (foto de perfil)
                            if not files:
                                c.execute("SELECT nombre FROM empleados WHERE foto_perfil LIKE ? LIMIT 1", (f"%{base_nom}%",))
                                row_emp = c.fetchone()
                                if row_emp:
                                    emp_clean = str(row_emp[0]).replace(" ", "_").strip()
                                    q3 = f"'{target_folder}' in parents and name contains '{emp_clean}' and trashed = false"
                                    res3 = service.files().list(q=q3, supportsAllDrives=True, includeItemsFromAllDrives=True, fields="files(id, name)", pageSize=3).execute()
                                    files = res3.get('files', []) if isinstance(res3, dict) else []

                            conn.close()
                    except Exception as e_db:
                        logger.warning(f"Error consultando DB para resolver foto en Drive {nombre_archivo}: {e_db}")

                if files:
                    file_id = files[0]['id']
                    with _drive_cache_lock:
                        _DRIVE_ID_CACHE[base_nom] = file_id
                        _DRIVE_ID_CACHE[token] = file_id
                else:
                    with _drive_cache_lock:
                        _DRIVE_MISSING_CACHE.add(token)
                        _DRIVE_MISSING_CACHE.add(base_nom)
                    return False
            except Exception as e_search:
                logger.warning(f"Error buscando foto en Drive {nombre_archivo}: {e_search}")
                return False

    # 3. Descargar bytes directamente con file_id
    try:
        req = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        os.makedirs(os.path.dirname(destino_local), exist_ok=True)

        temp_dest = destino_local + ".tmp"
        with open(temp_dest, "wb") as f:
            downloader = MediaIoBaseDownload(f, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        if os.path.exists(temp_dest) and os.path.getsize(temp_dest) > 100:
            os.replace(temp_dest, destino_local)
            logger.info(f"✅ Foto '{nombre_archivo}' descargada exitosamente de Drive (ID: {file_id})")
            return True
        else:
            if os.path.exists(temp_dest):
                try:
                    os.remove(temp_dest)
                except Exception:
                    pass
            return False
    except Exception as e:
        logger.warning(f"No se pudo descargar foto '{nombre_archivo}' de Drive: {e}")
        return False

