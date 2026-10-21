import os
import logging
import threading
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
_drive_lock = threading.Lock()


def obtener_servicio_drive():
    """Retorna el cliente de Google Drive autenticado con la cuenta de servicio."""
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


def subir_archivo_drive(ruta_local: str, nombre_remoto: str = None, carpeta_id: str = None, mimetype: str = None) -> dict:
    """
    Sube un archivo a Google Drive (compatible con Unidades Compartidas / Shared Drives).
    Retorna un diccionario con el status y el file_id o el error.
    """
    if not os.path.exists(ruta_local):
        return {"status": "error", "error": f"El archivo local no existe: {ruta_local}"}

    service = obtener_servicio_drive()
    if not service:
        return {"status": "error", "error": "No se pudo conectar con el servicio de Google Drive"}

    target_folder = carpeta_id or GDRIVE_FOLDER_ID
    if not target_folder:
        return {"status": "error", "error": "No se configuró el ID de carpeta de Google Drive"}

    nombre_archivo = nombre_remoto or os.path.basename(ruta_local)

    # Determinar mimetype básico
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
        media = MediaFileUpload(ruta_local, mimetype=mimetype, resumable=True)

        archivo = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink",
            supportsAllDrives=True
        ).execute()

        logger.info(f"✅ Archivo subido exitosamente a Google Drive: {nombre_archivo} (ID: {archivo.get('id')})")
        return {
            "status": "ok",
            "file_id": archivo.get("id"),
            "name": archivo.get("name"),
            "link": archivo.get("webViewLink")
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Error subiendo {nombre_archivo} a Google Drive: {error_msg}")
        return {"status": "error", "error": error_msg}


def subir_foto_async(ruta_local: str, nombre_remoto: str, carpeta_id: str = None):
    """Lanza la subida del archivo en un hilo secundario para no retrasar la respuesta del servidor."""
    def worker():
        try:
            subir_archivo_drive(ruta_local, nombre_remoto, carpeta_id)
        except Exception as e:
            logger.error(f"Error en worker de Google Drive: {e}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def realizar_respaldo_completo(carpeta_id: str = None) -> dict:
    """Sube una copia de la base de datos sqlite y los reportes a Google Drive."""
    db_path = os.path.join(BASE_DIR, "data", "asistencia.db")
    if not os.path.exists(db_path):
        return {"status": "error", "error": "Base de datos no encontrada"}

    fecha_hora = datetime.now().strftime("%Y-%m-%d_%H%M")
    nombre_db_backup = f"asistencia_backup_{fecha_hora}.db"

    res_db = subir_archivo_drive(db_path, nombre_remoto=nombre_db_backup, carpeta_id=carpeta_id)
    return res_db


def sincronizar_db_a_drive_master(carpeta_id: str = None) -> dict:
    """
    Sube o actualiza 'asistencia_master.db' en Google Drive.
    Si ya existe un archivo con ese nombre en la carpeta, lo actualiza en su lugar (sin duplicar).
    """
    db_path = os.path.join(BASE_DIR, "data", "asistencia.db")
    if not os.path.exists(db_path):
        return {"status": "error", "error": "Base de datos no encontrada"}

    service = obtener_servicio_drive()
    if not service:
        return {"status": "error", "error": "No hay conexión con Google Drive"}

    target_folder = carpeta_id or GDRIVE_FOLDER_ID
    if not target_folder:
        return {"status": "error", "error": "No hay ID de carpeta de Drive"}

    try:
        q = f"'{target_folder}' in parents and name = 'asistencia_master.db' and trashed = false"
        res = service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get('files', [])
        media = MediaFileUpload(db_path, mimetype="application/x-sqlite3", resumable=True)

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
        return {"status": "error", "error": str(e)}


def sincronizar_db_a_drive_master_async(carpeta_id: str = None):
    """Lanza la sincronización de asistencia_master.db en un hilo en segundo plano."""
    def worker():
        try:
            sincronizar_db_a_drive_master(carpeta_id)
        except Exception as e:
            logger.error(f"Error en worker de sincronización a Drive: {e}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def restaurar_db_desde_drive_master(carpeta_id: str = None) -> bool:
    """
    Descarga 'asistencia_master.db' desde Google Drive al archivo local data/asistencia.db
    si la base de datos local está vacía o no existe.
    """
    db_path = os.path.join(BASE_DIR, "data", "asistencia.db")
    service = obtener_servicio_drive()
    if not service:
        logger.warning("No se pudo conectar a Google Drive para restaurar base de datos.")
        return False

    target_folder = carpeta_id or GDRIVE_FOLDER_ID
    try:
        q = f"'{target_folder}' in parents and name = 'asistencia_master.db' and trashed = false"
        res = service.files().list(q=q, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get('files', [])
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
            if os.path.exists(db_path):
                os.remove(db_path)
            os.rename(temp_path, db_path)
            logger.info(f"✅ Base de datos restaurada con éxito desde Google Drive ('asistencia_master.db', ID: {file_id}).")
            return True
        else:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False
    except Exception as e:
        logger.error(f"❌ Error restaurando base de datos desde Google Drive: {e}")
        return False
