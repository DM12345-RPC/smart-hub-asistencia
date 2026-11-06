import os
import json
import logging
import threading
import base64
import sqlite3

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "asistencia.db")
VAPID_KEY_FILE = os.path.join(os.path.dirname(__file__), "vapid_private.pem")

_VAPID_PUBLIC_KEY = None
_VAPID_LOCK = threading.Lock()

def _asegurar_claves_vapid():
    """Garantiza la existencia de las claves VAPID y extrae la clave pública."""
    global _VAPID_PUBLIC_KEY
    with _VAPID_LOCK:
        if _VAPID_PUBLIC_KEY:
            return _VAPID_PUBLIC_KEY

        from py_vapid import Vapid
        from cryptography.hazmat.primitives import serialization

        if not os.path.exists(VAPID_KEY_FILE):
            v = Vapid()
            v.generate_keys()
            priv_pem = v.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            with open(VAPID_KEY_FILE, "wb") as f:
                f.write(priv_pem)
            logger.info("🔑 Claves VAPID generadas y guardadas en vapid_private.pem")
        else:
            v = Vapid.from_file(VAPID_KEY_FILE)

        pub_bytes = v.public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
        _VAPID_PUBLIC_KEY = base64.urlsafe_b64encode(pub_bytes).decode("utf-8").rstrip("=")
        return _VAPID_PUBLIC_KEY


def obtener_vapid_public_key() -> str:
    """Retorna la clave pública VAPID en Base64 URL-safe para que el navegador se suscriba."""
    return _asegurar_claves_vapid()


def _ejecutar_envio_push(empleados: list, titulo: str, mensaje: str, url: str):
    """Loop en segundo plano que despacha los Web Push a los teléfonos registrados."""
    try:
        import pywebpush
        from pywebpush import webpush, WebPushException

        _asegurar_claves_vapid()

        try:
            from .database import get_connection, normalizar_nombre
        except ImportError:
            from database import get_connection, normalizar_nombre

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT empleado_nombre, subscription_json FROM push_subscriptions")
        filas = cursor.fetchall()
        conn.close()

        if not filas:
            logger.info("Aviso WebPush: No hay celulares suscritos aún en la base de datos.")
            return

        # Filtrado de destinatarios tolerante a tildes y mayúsculas/minúsculas
        if not empleados or empleados == "ALL" or "ALL" in empleados:
            destinatarios_filas = filas
        else:
            targets = {normalizar_nombre(e) for e in empleados if e and str(e).strip()}
            destinatarios_filas = [
                r for r in filas 
                if normalizar_nombre(r["empleado_nombre"] if hasattr(r, "keys") else r[0]) in targets
            ]

        if not destinatarios_filas:
            logger.info(f"Aviso WebPush: Ninguno de los destinatarios {empleados} tiene celular suscrito.")
            return

        payload_json = json.dumps({
            "title": titulo,
            "body": mensaje,
            "url": url or "/"
        }, ensure_ascii=False)

        subs_invalidas = []

        for row in destinatarios_filas:
            emp_nombre = row["empleado_nombre"] if hasattr(row, "keys") else row[0]
            sub_str = row["subscription_json"] if hasattr(row, "keys") else row[1]
            try:
                sub_info = json.loads(sub_str) if isinstance(sub_str, str) else sub_str
                # Si sub_info vino anidado
                if "endpoint" not in sub_info and "subscription" in sub_info:
                    sub_info = sub_info["subscription"]
                    if isinstance(sub_info, str):
                        sub_info = json.loads(sub_info)

                if not sub_info.get("endpoint"):
                    continue

                webpush(
                    subscription_info=sub_info,
                    data=payload_json,
                    vapid_private_key=VAPID_KEY_FILE,
                    vapid_claims={"sub": "mailto:soporte@ryanpacific.com"},
                    timeout=10
                )
                logger.info(f"📲 Notificación Push enviada a '{emp_nombre}' en su celular.")
            except WebPushException as ex:
                logger.warning(f"Aviso WebPush a '{emp_nombre}': {ex}")
                # Código 404 o 410 indica que el celular se desuscribió o cambió token
                if hasattr(ex, "response") and ex.response is not None and ex.response.status_code in (404, 410):
                    subs_invalidas.append(emp_nombre)
            except Exception as e_indiv:
                logger.warning(f"Error enviando WebPush a '{emp_nombre}': {e_indiv}")

        if subs_invalidas:
            try:
                c2 = get_connection()
                pl = ",".join(["?"] * len(subs_invalidas))
                c2.execute(f"DELETE FROM push_subscriptions WHERE empleado_nombre IN ({pl})", subs_invalidas)
                c2.commit()
                c2.close()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error general en despacho de WebPush: {e}")


def enviar_webpush_async(empleados=None, titulo="📢 Notificación Ryan Pacific", mensaje="Tienes un nuevo aviso de la empresa.", url="/"):
    """
    Despacha notificaciones Push a los teléfonos en segundo plano (no bloquea el servidor).
    Despierta celulares en el bolsillo (Android y iPhone) con sonido y vibración.
    """
    t = threading.Thread(
        target=_ejecutar_envio_push,
        args=(empleados, titulo, mensaje, url),
        daemon=True,
        name="WebPushDispatcher"
    )
    t.start()
