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

        # 2. Secret File en Render (/etc/secrets/service_account.json)
        secret_file = "/etc/secrets/service_account.json"
        if not creds and os.path.exists(secret_file):
            try:
                creds = service_account.Credentials.from_service_account_file(secret_file, scopes=SCOPES)
            except Exception as e:
                logger.error(f"Error cargando credenciales de {secret_file}: {e}")

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
