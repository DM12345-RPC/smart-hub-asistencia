import os
import io
import json
import base64
import uuid
import threading
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import cv2
import qrcode
from PIL import Image

from .database import (
    init_db,
    guardar_o_actualizar_empleado,
    obtener_empleado,
    listar_empleados,
    eliminar_empleado,
    listar_proyectos,
    obtener_proyecto_por_nombre,
    obtener_proyecto_por_qr,
    guardar_proyecto,
    actualizar_config_proyecto,
    eliminar_proyecto,
    registrar_marcaje,
    listar_marcajes,
    reasignar_proyecto_marcaje,
    eliminar_marcaje,
    obtener_metricas_dashboard,
    actualizar_rol_empleado,
    obtener_rol_empleado,
    actualizar_obras_empleado,
    obtener_obras_empleado,
    obtener_estado_hoy_empleado,
    obtener_resumen_dia_foreman,
    asignar_actividad_empleados,
    cerrar_actividades_abiertas_empleado,
    obtener_reporte_actividades_costos,
    obtener_historial_actividades_empleado_dia,
    forzar_reenrolamiento_empleados,
    registrar_consentimiento_biometrico,
    actualizar_permiso_horas_proyecto,
    actualizar_permiso_horas_empleado,
    obtener_resumen_horas_obrero,
    obtener_directorio_personal,
    crear_comunicado,
    eliminar_comunicado,
    actualizar_comunicado,
    obtener_comunicados_pendientes_empleado,
    obtener_mis_tareas_y_comunicados,
    registrar_lectura_comunicado,
    listar_comunicados_admin,
    guardar_suscripcion_push,
    obtener_ahora_seattle,
    obtener_fecha_seattle_hoy,
    obtener_hora_seattle_ahora
)
from .geofence import validar_posicion_en_obra
from .biometrics import decodificar_imagen, extraer_vector_rostro, verificar_1_a_1, detectar_borrosidad

# Rutas de almacenamiento
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(BASE_DIR, "data")
CAPTURAS_DIR = os.path.join(DATA_DIR, "capturas")
PERFILES_DIR = os.path.join(DATA_DIR, "perfiles")
PERSONAL_TXT_PATH = os.path.join(BASE_DIR, "personal.txt")

os.makedirs(CAPTURAS_DIR, exist_ok=True)
os.makedirs(PERFILES_DIR, exist_ok=True)

# Restaurar base de datos maestra desde Google Drive al inicio
try:
    from .gdrive import restaurar_db_desde_drive_master, sincronizar_db_a_drive_master_async, cargar_indice_drive
    db_file = os.path.join(DATA_DIR, "asistencia.db")
    print("[GDRIVE] Sincronizando con Google Drive al inicio...")
    restaurado = restaurar_db_desde_drive_master()
    if restaurado:
        print("[GDRIVE] [OK] Base de datos maestra sincronizada exitosamente desde Google Drive.")
    else:
        print("[GDRIVE] Info: Manteniendo base de datos existente o inicializando nueva.")
    
    # Precargar catálogo en memoria para descargas ultra-rápidas O(1)
    cargar_indice_drive()
except Exception as e:
    print("[GDRIVE] Aviso: No se pudo verificar sincronización en Drive:", e)

# Iniciar Base de Datos y migraciones
init_db()

app = FastAPI(title="Smart Hub - Control de Asistencia Ryan Pacific", version="2.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir archivos estáticos
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def raiz_pwa():
    """Servir la Progressive Web App para los obreros en sus celulares."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/admin")
@app.get("/admin/")
def panel_administracion():
    """Servir el panel de control administrativo y auditoría de nómina."""
    return FileResponse(os.path.join(STATIC_DIR, "admin.html"))

@app.get("/admin/{path:path}")
def panel_administracion_subpath(path: str):
    """Garantizar que cualquier subruta de /admin en móviles cargue el panel."""
    return FileResponse(os.path.join(STATIC_DIR, "admin.html"))

# ---------------- API ROLES Y RESUMEN FOREMAN / ADMIN ----------------

@app.get("/api/empleado_info")
def api_empleado_info(nombre: str = Query(...)):
    """Retorna información del empleado, incluyendo rol, obras, permisos, tarea actual y si requiere re-enrolamiento."""
    emp = obtener_empleado(nombre)
    estado_hoy = obtener_estado_hoy_empleado(nombre)
    info_extra = obtener_mis_tareas_y_comunicados(nombre)
    tarea_actual = info_extra.get("tarea_actual")
    pendientes_comunicados = info_extra.get("pendientes_count", 0)
    
    if not emp:
        # Personal nuevo o aún no registrado: forzar enrolamiento biométrico antes de marcar
        return {
            "status": "ok",
            "exists": False,
            "nombre": nombre,
            "rol": "worker",
            "obras_asignadas": "ALL",
            "telefono": "",
            "enrolado": False,
            "requiere_reenrolamiento": True,
            "bloquear_horas_resumen": False,
            "consentimiento_biometrico": False,
            "estado_hoy": estado_hoy,
            "tarea_actual": tarea_actual,
            "pendientes_comunicados": pendientes_comunicados
        }

    rol = emp.get("rol", "worker") or "worker"
    obras = emp.get("obras_asignadas", "ALL") or "ALL"
    bloquear_horas = emp.get("bloquear_horas_resumen", 0)
    consentimiento = emp.get("consentimiento_biometrico", 0)
    vectores = emp.get("vectores", [])
    
    # Enrolado si tiene vectores faciales y no tiene pendiente el re-enrolamiento
    enrolado = bool(len(vectores) > 0 and emp.get("requiere_reenrolamiento", 0) == 0)
    requiere_reenrolamiento = bool(emp.get("requiere_reenrolamiento", 0) or not enrolado)

    return {
        "status": "ok",
        "exists": True,
        "nombre": emp.get("nombre", nombre),
        "rol": rol,
        "obras_asignadas": obras,
        "telefono": emp.get("telefono", ""),
        "enrolado": enrolado,
        "requiere_reenrolamiento": requiere_reenrolamiento,
        "bloquear_horas_resumen": bool(bloquear_horas),
        "consentimiento_biometrico": bool(consentimiento),
        "estado_hoy": estado_hoy,
        "tarea_actual": tarea_actual,
        "pendientes_comunicados": pendientes_comunicados
    }

@app.post("/api/empleados/{empleado_id}/rol")
def api_actualizar_rol(empleado_id: int, payload: dict):
    """Actualiza el rol del empleado (worker, foreman, admin)."""
    nuevo_rol = payload.get("rol", "worker")
    actualizar_rol_empleado(empleado_id, nuevo_rol)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {"status": "ok", "empleado_id": empleado_id, "nuevo_rol": nuevo_rol}

@app.post("/api/empleados/{empleado_id}/obras")
def api_actualizar_obras(empleado_id: int, payload: dict):
    """Actualiza las obras asignadas a un empleado ('ALL' o nombres separados por coma)."""
    obras = payload.get("obras", "ALL")
    if isinstance(obras, list):
        obras = ", ".join([str(o).strip() for o in obras if str(o).strip()])
    if not obras or not str(obras).strip():
        obras = "ALL"
    actualizar_obras_empleado(empleado_id, str(obras).strip())
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {"status": "ok", "empleado_id": empleado_id, "obras_asignadas": str(obras).strip()}

@app.get("/api/resumen_dia")
def api_resumen_dia(foreman: Optional[str] = Query(None)):
    """Retorna el resumen del día agrupado por obra para el Foreman y Admin."""
    return obtener_resumen_dia_foreman(foreman_nombre=foreman)


# ---------------- API LISTA OFICIAL DE PERSONAL ----------------

@app.get("/api/personal_oficial")
def api_personal_oficial():
    """Retorna la lista oficial de nombres de personal.txt para autocompletar."""
    nombres = []
    if os.path.exists(PERSONAL_TXT_PATH):
        try:
            with open(PERSONAL_TXT_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        nombres.append(line)
        except Exception as e:
            print("Error leyendo personal.txt con utf-8, intentando latin-1:", e)
            try:
                with open(PERSONAL_TXT_PATH, "r", encoding="latin-1") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            nombres.append(line)
            except Exception:
                pass

    # Quitar duplicados conservando orden
    vistos = set()
    nombres_unicos = []
    for n in nombres:
        norm = n.strip()
        if norm.lower() not in vistos:
            vistos.add(norm.lower())
            nombres_unicos.append(norm)

    return {"status": "ok", "total": len(nombres_unicos), "personal": nombres_unicos}


# ---------------- API DE PROYECTOS Y GEOFENCING ----------------

@app.get("/api/proyectos")
def api_listar_proyectos():
    return {"status": "ok", "proyectos": listar_proyectos()}


@app.post("/api/proyectos")
def api_guardar_proyecto(
    nombre: str = Form(...),
    lat: float = Form(...),
    lon: float = Form(...),
    radio_metros: int = Form(150),
    qr_token: Optional[str] = Form(None),
    pisos: Optional[str] = Form(""),
    actividades: Optional[str] = Form("")
):
    if not qr_token:
        codigo_limpio = "".join([c for c in nombre.upper() if c.isalnum()])[:8]
        qr_token = f"RPCS-{codigo_limpio}-{uuid.uuid4().hex[:6].upper()}"

    guardar_proyecto(nombre, lat, lon, radio_metros, qr_token, pisos=pisos or "", actividades=actividades or "")
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {"status": "ok", "mensaje": "Proyecto guardado", "qr_token": qr_token}


@app.post("/api/proyectos/{proyecto_id}/config")
async def api_actualizar_config_proyecto(proyecto_id: int, payload: dict):
    pisos = payload.get("pisos", "").strip()
    actividades = payload.get("actividades", "").strip()
    actualizar_config_proyecto(proyecto_id, pisos, actividades)
    if "mostrar_horas_obreros" in payload:
        mostrar = int(payload.get("mostrar_horas_obreros", 1))
        actualizar_permiso_horas_proyecto(proyecto_id, mostrar)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {"status": "ok", "mensaje": "Pisos, actividades y permisos actualizados correctamente"}

@app.post("/api/proyectos/{proyecto_id}/permiso_horas")
def api_proyecto_permiso_horas(proyecto_id: int, payload: dict):
    mostrar = int(payload.get("mostrar_horas_obreros", 1))
    actualizar_permiso_horas_proyecto(proyecto_id, mostrar)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {"status": "ok", "proyecto_id": proyecto_id, "mostrar_horas_obreros": mostrar}


@app.delete("/api/proyectos/{proyecto_id}")
def api_eliminar_proyecto(proyecto_id: int):
    eliminar_proyecto(proyecto_id)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {"status": "ok", "mensaje": "Proyecto eliminado"}


@app.get("/api/qr_proyecto/{proyecto_id}")
def api_generar_qr_imprimible(proyecto_id: int):
    """Genera un cartel oficial en imagen PNG con el QR para pegar en la entrada de la obra."""
    proyectos = listar_proyectos()
    proy = next((p for p in proyectos if p["id"] == proyecto_id), None)
    if not proy:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    qr_data = proy["qr_token"]
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=4,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="#0f172a", back_color="white").convert("RGB")

    logo_path = os.path.join(STATIC_DIR, "icon.png")
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path)
            logo_size = (img_qr.size[0] // 4, img_qr.size[1] // 4)
            logo = logo.resize(logo_size, Image.Resampling.LANCZOS)
            pos = ((img_qr.size[0] - logo_size[0]) // 2, (img_qr.size[1] - logo_size[1]) // 2)
            img_qr.paste(logo, pos)
        except Exception as e:
            print("No se pudo incrustar logo en QR:", e)

    buffer = io.BytesIO()
    img_qr.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


# ---------------- API DE EMPLEADOS Y BIOMETRÍA ----------------

@app.get("/api/empleados")
def api_listar_empleados(todos: bool = False):
    return {"status": "ok", "empleados": listar_empleados(solo_enrolados=not todos)}


@app.delete("/api/empleados/{empleado_id}")
def api_eliminar_empleado(empleado_id: int):
    eliminar_empleado(empleado_id)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {"status": "ok", "mensaje": "Trabajador eliminado de la base de datos"}


@app.post("/api/empleados/forzar_reenrolamiento")
def api_forzar_reenrolamiento(payload: dict):
    """Permite invalidar biometrías obsoletas para forzar nueva foto de perfil."""
    empleado_ids = payload.get("empleado_ids", [])
    todos = bool(payload.get("todos", False) or (payload.get("forzar") and not empleado_ids))
    res = forzar_reenrolamiento_empleados(empleado_ids, todos)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return res


@app.post("/api/empleados/{empleado_id}/permiso_horas")
def api_empleado_permiso_horas(empleado_id: int, payload: dict):
    """Bloquea o permite la visualización de horas trabajadas para un empleado específico."""
    bloquear_val = payload.get("bloquear_horas_resumen")
    if bloquear_val is None:
        bloquear_val = payload.get("bloquear", 0)
    bloquear = 1 if bool(bloquear_val) else 0
    actualizar_permiso_horas_empleado(empleado_id, bloquear)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {"status": "ok", "empleado_id": empleado_id, "bloquear_horas_resumen": bloquear}


@app.get("/api/empleados/directorio_csv")
def api_descargar_directorio_csv():
    """Genera y descarga el archivo CSV con la lista del personal y sus teléfonos para importar a nómina o CRM."""
    personal = obtener_directorio_personal()
    encabezados = [
        "ID", "Nombre Completo", "Teléfono", "Puesto", "Rol en Sistema",
        "Obras Asignadas", "Biometría Facial", "Permiso Ver Horas",
        "Consentimiento Legal Biometría", "Fecha Registro"
    ]
    lineas = [";".join(encabezados)]
    for p in personal:
        enrolado_txt = "ENROLADO" if p.get("enrolado") == 1 else "PENDIENTE / REQUERIDO"
        horas_txt = "BLOQUEADO" if p.get("bloquear_horas_resumen") == 1 else "PERMITIDO"
        consent_txt = "AUTORIZADO (RCW 19.375)" if p.get("consentimiento_biometrico") == 1 else "PENDIENTE"
        fila = [
            str(p.get("id", "")),
            f'"{p.get("nombre", "")}"',
            f'"{p.get("telefono", "")}"',
            f'"{p.get("puesto", "")}"',
            f'"{p.get("rol", "")}"',
            f'"{p.get("obras_asignadas", "")}"',
            enrolado_txt,
            horas_txt,
            consent_txt,
            f'"{p.get("fecha_registro", "")}"'
        ]
        lineas.append(";".join(fila))

    csv_bytes = ("\ufeff" + "\r\n".join(lineas)).encode("utf-8")
    ahora_str = obtener_fecha_seattle_hoy()
    headers = {
        "Content-Disposition": f'attachment; filename="Directorio_Personal_Ryan_Pacific_{ahora_str}.csv"'
    }
    return StreamingResponse(io.BytesIO(csv_bytes), media_type="text/csv; charset=utf-8", headers=headers)


@app.post("/api/obrero/consentimiento_biometrico")
def api_consentimiento_biometrico(payload: dict):
    """Guarda la autorización legal formal de biometría del obrero según ley RCW 19.375 de Washington State."""
    nombre = payload.get("nombre", "").strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="Nombre requerido")
    res = registrar_consentimiento_biometrico(nombre)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return res


@app.get("/api/obrero/mis_horas")
def api_obrero_mis_horas(
    nombre: str = Query(...),
    desde: Optional[str] = Query(None),
    hasta: Optional[str] = Query(None)
):
    """Permite al obrero ver su resumen de horas si la obra y su usuario lo tienen autorizado."""
    return obtener_resumen_horas_obrero(nombre, desde, hasta)


@app.post("/api/enrolar")
async def api_enrolar_empleado(
    nombre: str = Form(...),
    telefono: str = Form(""),
    puesto: str = Form("Obrero / Campo"),
    files: List[UploadFile] = File(...)
):
    """
    Enrolamiento Multi-Foto (1 a 3 fotos):
    Guarda la foto oficial en data/perfiles/ y extrae los vectores matemáticos de 512D.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Debe enviar al menos una foto.")

    vectores = []
    errores = []
    ruta_foto_oficial = None

    for idx, f in enumerate(files):
        bytes_data = await f.read()
        img = decodificar_imagen(bytes_data)
        if img is None:
            errores.append(f"Foto {idx+1}: Formato no válido")
            continue

        emb, err, bbox = extraer_vector_rostro(img)
        if err:
            errores.append(f"Foto {idx+1}: {err}")
        else:
            vectores.append(emb.tolist())

            # Guardar la primera foto válida como foto oficial de perfil
            if not ruta_foto_oficial:
                fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                nombre_archivo = f"perfil_{fecha_str}_{uuid.uuid4().hex[:6]}.jpg"
                ruta_disco = os.path.join(PERFILES_DIR, nombre_archivo)
                cv2.imwrite(ruta_disco, img)
                ruta_foto_oficial = f"/data/perfiles/{nombre_archivo}"

    if not vectores:
        return {
            "status": "error",
            "error": "No se detectó un rostro claro en ninguna foto: " + "; ".join(errores)
        }

    guardar_o_actualizar_empleado(nombre, telefono, puesto, vectores, foto_perfil=ruta_foto_oficial)
    
    # Respaldo de foto de perfil oficial en Drive
    if ruta_foto_oficial and 'ruta_disco' in locals() and os.path.exists(ruta_disco):
        try:
            from .gdrive import subir_foto_async
            subir_foto_async(ruta_disco, f"PERFIL_{nombre.replace(' ', '_')}_{nombre_archivo}")
        except Exception:
            pass

    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return {
        "status": "ok",
        "mensaje": f"Trabajador '{nombre}' enrolado exitosamente ({len(vectores)} perfiles biométricos guardados).",
        "total_vectores": len(vectores),
        "foto_perfil": ruta_foto_oficial,
        "advertencias": errores if errores else None
    }


# ---------------- API DE ASISTENCIA Y MARCAJE ----------------

# Cerrojo global FIFO para marcajes: pone en estricta fila india cualquier ráfaga de marcajes.
# Incluso si 100 obreros pican 'marcar' al mismo milisegundo, se atienden en fila ordenada en memoria (~50ms cada uno),
# garantizando cero caídas de CPU, cero saturación de RAM y cero bloqueos de base de datos.
_marcaje_lock = threading.Lock()

def procesar_un_marcaje(payload: dict) -> dict:
    """Función central que evalúa biometría 1:1, geocerca/QR y auditoría, con fila india garantizada."""
    with _marcaje_lock:
        return _procesar_un_marcaje_interno(payload)

def _procesar_un_marcaje_interno(payload: dict) -> dict:
    nombre = payload.get("empleado_nombre", "").strip()
    tipo = payload.get("tipo", "ENTRADA").upper()
    proy_nombre = payload.get("proyecto_nombre", "")
    timestamp_local = payload.get("timestamp_local") or obtener_ahora_seattle().isoformat()
    lat = payload.get("lat")
    lon = payload.get("lon")
    accuracy = payload.get("gps_accuracy")
    qr_token = payload.get("qr_token")
    foto_b64 = payload.get("foto")
    offline = 1 if payload.get("offline_sincronizado") else 0

    empleado = obtener_empleado(nombre)

    # 0. Verificación de Biometría Obligatoria (Nuevo personal o Re-enrolamiento):
    # Si es SALIDA y ya tiene una ENTRADA hoy, permitirle salir para no perjudicar su registro de jornada.
    # Si es ENTRADA o no está en jornada activa, exigir enrolamiento biométrico.
    necesita_biometria = False
    if not empleado or not empleado.get("vectores") or empleado.get("requiere_reenrolamiento") == 1:
        necesita_biometria = True

    if necesita_biometria:
        # Permitir salida de jornada activa si ya marcó entrada hoy
        estado_hoy_actual = obtener_estado_hoy_empleado(nombre)
        ya_esta_trabajando = (estado_hoy_actual and estado_hoy_actual.get("estado") == "ACTIVO")
        
        if not (tipo == "SALIDA" and ya_esta_trabajando):
            return {
                "status": "error",
                "error_code": "REQUIERE_REENROLAMIENTO",
                "error": f"Hola {nombre}. La empresa requiere tu registro biométrico facial oficial antes de iniciar tu jornada. Por favor toma tu foto oficial ahora."
            }

    # 0.1 Verificación Antidoble Marcaje: Impedir registrar ENTRADA si ya está ACTIVO hoy en Seattle
    if tipo == "ENTRADA":
        estado_hoy = obtener_estado_hoy_empleado(nombre)
        if estado_hoy and estado_hoy["estado"] == "ACTIVO":
            hora_prev = estado_hoy.get("hora") or "hora anterior"
            proy_prev = estado_hoy.get("proyecto") or "la obra"
            return {
                "status": "error",
                "error_code": "DOBLE_ENTRADA",
                "error": f"Ya tienes una ENTRADA registrada hoy a las {hora_prev} en {proy_prev}. No se permite registrar doble entrada; debes marcar SALIDA al terminar tu jornada.",
                "hora_marcada": hora_prev,
                "proyecto": proy_prev
            }

    # 1. Guardar foto de marcaje en disco
    nombre_archivo_foto = None
    similitud = 0.0
    error_facial = None

    if foto_b64 and len(foto_b64) > 100:
        try:
            img = decodificar_imagen(foto_b64)
            if img is not None and img.shape[0] > 10 and img.shape[1] > 10:
                fecha_str = obtener_ahora_seattle().strftime("%Y%m%d_%H%M%S")
                nombre_archivo = f"punch_{fecha_str}_{uuid.uuid4().hex[:6]}.jpg"
                ruta_completa = os.path.join(CAPTURAS_DIR, nombre_archivo)
                
                # Guardar foto físicamente
                guardado = cv2.imwrite(ruta_completa, img)
                if guardado:
                    nombre_archivo_foto = f"/data/capturas/{nombre_archivo}"

                # Extraer vector biométrico y comparar 1:1
                if empleado and empleado.get("vectores"):
                    emb, err, bbox = extraer_vector_rostro(img)
                    if err:
                        error_facial = err
                    else:
                        similitud = verificar_1_a_1(emb, empleado["vectores"])
                else:
                    error_facial = "Empleado no enrolado previamente"
        except Exception as e:
            error_facial = f"Error decodificando foto: {str(e)}"
    else:
        error_facial = "No se recibió imagen de la cámara"

    # 2. Validación de Ubicación (Geocerca o QR de Entrada de Obra)
    metodo_ubicacion = "DESCONOCIDO"
    distancia_m = None
    es_valido_gps = False
    motivo_ubicacion = ""

    # Caso A: QR de Obra Físico (Bajo Losa / Sótano)
    if qr_token:
        proy_qr = obtener_proyecto_por_qr(qr_token)
        if proy_qr and proy_qr["nombre"] == proy_nombre:
            metodo_ubicacion = "QR_OBRA"
            es_valido_gps = True
            distancia_m = 0.0
            motivo_ubicacion = "Verificado por QR físico en acceso de obra (Bajo losa)"
        else:
            motivo_ubicacion = "QR de obra inválido"

    # Caso B: Coordenadas Satelitales GPS
    if not es_valido_gps and lat is not None and lon is not None:
        proy = obtener_proyecto_por_nombre(proy_nombre)
        if proy:
            es_valido_gps, dist, motivo = validar_posicion_en_obra(
                lat, lon, accuracy, proy["lat"], proy["lon"], proy["radio_metros"]
            )
            distancia_m = dist
            motivo_ubicacion = motivo
            metodo_ubicacion = "GPS"
        else:
            motivo_ubicacion = f"Proyecto '{proy_nombre}' no encontrado"

    # 3. Reglas de Auditoría Inteligente (Semáforo Antifraude)
    UMBRAL_OPTIMO = 0.50
    UMBRAL_DUDOSO = 0.40

    if not empleado:
        estado_auditoria = "REVISION"
        motivo_ubicacion = f"Trabajador sin enrolar | {motivo_ubicacion}"
    elif similitud >= UMBRAL_OPTIMO and es_valido_gps:
        estado_auditoria = "APROBADO"
    elif similitud < UMBRAL_DUDOSO and nombre_archivo_foto:
        estado_auditoria = "SOSPECHA_FRAUDE"
        motivo_ubicacion = f"Posible suplantación ({round(similitud*100, 1)}% coincidencia) | {motivo_ubicacion}"
    elif not es_valido_gps:
        if distancia_m and distancia_m > 1000:
            estado_auditoria = "SOSPECHA_FRAUDE"
            motivo_ubicacion = f"Marcaje a más de 1 km de la obra ({round(distancia_m/1000, 1)} km)"
        else:
            estado_auditoria = "REVISION_GPS"
    elif UMBRAL_DUDOSO <= similitud < UMBRAL_OPTIMO:
        estado_auditoria = "REVISION_FACIAL"
        motivo_ubicacion = f"Rostro parcialmente cubierto o baja luz ({round(similitud*100, 1)}%)"
    else:
        estado_auditoria = "REVISION"

    # Registrar en Base de Datos SQLite
    marcaje_id = registrar_marcaje({
        "empleado_nombre": nombre,
        "tipo": tipo,
        "timestamp_local": timestamp_local,
        "timestamp_servidor": datetime.now().isoformat(),
        "proyecto_nombre": proy_nombre,
        "lat": lat,
        "lon": lon,
        "distancia_metros": distancia_m,
        "gps_accuracy": accuracy,
        "metodo_ubicacion": metodo_ubicacion,
        "similitud_facial": round(similitud * 100, 2),
        "estado_auditoria": estado_auditoria,
        "foto_path": nombre_archivo_foto,
        "offline_sincronizado": offline,
        "notas": motivo_ubicacion
    })

    # Si es SALIDA, cerrar cualquier actividad abierta del día para este empleado
    if tipo.upper() == "SALIDA":
        try:
            cerrar_actividades_abiertas_empleado(nombre, timestamp_local)
        except Exception as e:
            print("Error cerrando actividad abierta:", e)

    # Respaldo automático de la foto en Google Drive en segundo plano
    if nombre_archivo_foto and ruta_completa and os.path.exists(ruta_completa):
        try:
            from backend.gdrive import subir_foto_async
            fecha_hora_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            nombre_limpio_emp = nombre.replace(" ", "_")
            nombre_foto_drive = f"{fecha_hora_str}_{tipo}_{proy_nombre}_{nombre_limpio_emp}_{nombre_archivo}.jpg"
            subir_foto_async(ruta_completa, nombre_foto_drive)
        except Exception as e_gdrive:
            print("Aviso: no se pudo iniciar subida a Google Drive:", e_gdrive)

    # Sincronizar base de datos maestra con Google Drive en segundo plano
    try:
        from backend.gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass

    return {
        "status": "ok",
        "marcaje_id": marcaje_id,
        "empleado": nombre,
        "tipo": tipo,
        "hora_marcada": timestamp_local,
        "similitud_facial": round(similitud * 100, 2),
        "distancia_metros": distancia_m,
        "foto_path": nombre_archivo_foto,
        "metodo_ubicacion": metodo_ubicacion,
        "estado_auditoria": estado_auditoria,
        "mensaje": "Marcaje guardado correctamente."
    }


@app.post("/api/marcar")
async def api_marcar_asistencia(
    empleado_nombre: str = Form(...),
    tipo: str = Form(...),
    proyecto_nombre: str = Form(...),
    timestamp_local: str = Form(...),
    lat: Optional[float] = Form(None),
    lon: Optional[float] = Form(None),
    gps_accuracy: Optional[float] = Form(None),
    qr_token: Optional[str] = Form(None),
    foto: Optional[str] = Form(None)
):
    payload = {
        "empleado_nombre": empleado_nombre,
        "tipo": tipo,
        "proyecto_nombre": proyecto_nombre,
        "timestamp_local": timestamp_local,
        "lat": lat,
        "lon": lon,
        "gps_accuracy": gps_accuracy,
        "qr_token": qr_token,
        "foto": foto,
        "offline_sincronizado": False
    }
    return procesar_un_marcaje(payload)


@app.post("/api/sincronizar_lote")
async def api_sincronizar_lote(lote: List[dict]):
    resultados = []
    for item in lote:
        item["offline_sincronizado"] = True
        try:
            res = procesar_un_marcaje(item)
            resultados.append(res)
        except Exception as e:
            resultados.append({"status": "error", "error": str(e), "item_ref": item.get("id_local")})

    return {"status": "ok", "total_procesados": len(resultados), "resultados": resultados}


@app.delete("/api/marcajes/{marcaje_id}")
def api_eliminar_marcaje(marcaje_id: int):
    """Permite al equipo administrativo borrar registros erróneos o de prueba."""
    eliminar_marcaje(marcaje_id)
    try:
        from backend.gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception as e:
        logger.warning(f"No se pudo sincronizar a Drive tras eliminar marcaje: {e}")
    return {"status": "ok", "mensaje": "Marcaje eliminado correctamente"}


@app.post("/api/marcajes/{marcaje_id}/reasignar_proyecto")
def api_reasignar_proyecto_marcaje(marcaje_id: int, payload: dict):
    """Permite a los administradores corregir la obra asignada a un trabajador si se equivocó al marcar."""
    nuevo_proyecto = payload.get("nuevo_proyecto", "").strip()
    reasignar_todos_hoy = bool(payload.get("reasignar_todos_hoy", False))
    if not nuevo_proyecto:
        raise HTTPException(status_code=400, detail="Debe especificar la obra destino.")
    res = reasignar_proyecto_marcaje(marcaje_id, nuevo_proyecto, reasignar_todos_hoy)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("error", "Error reasignando proyecto"))
    try:
        from backend.gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception as e:
        logger.warning(f"No se pudo sincronizar a Drive tras reasignar proyecto: {e}")
    return res


# ---------------- API DEL PANEL ADMINISTRATIVO ----------------

@app.get("/api/dashboard_metricas")
def api_metricas(proyecto: Optional[str] = Query(None)):
    return obtener_metricas_dashboard(proyecto_nombre=proyecto)


@app.get("/api/reportes")
def api_reportes(
    limit: int = 150,
    estado: Optional[str] = "TODOS",
    fecha: Optional[str] = None
):
    marcajes = listar_marcajes(limit=limit, filtro_estado=estado, filtro_fecha=fecha)
    return {"status": "ok", "total": len(marcajes), "marcajes": marcajes}


# ---------------- API DE ACTIVIDADES Y ASIGNACIONES DE PISO ----------------

@app.post("/api/actividades/asignar")
async def api_asignar_actividades(payload: dict):
    empleados = payload.get("empleados", [])
    proyecto = payload.get("proyecto_nombre", "")
    piso = payload.get("piso", "").strip()
    actividad = payload.get("actividad", "").strip()
    asignado_por = payload.get("asignado_por", "Foreman")
    hora = payload.get("hora", None)
    notas = payload.get("notas", "")

    if not empleados or not piso or not actividad:
        raise HTTPException(status_code=400, detail="Debe especificar al menos un trabajador, piso y actividad.")

    # Validar que si el supervisor tiene obras asignadas específicas, no asigne en otra obra
    if asignado_por and asignado_por != "Admin":
        foreman_emp = obtener_empleado(asignado_por)
        if foreman_emp:
            obras_raw = foreman_emp.get("obras_asignadas", "ALL") or "ALL"
            if obras_raw.strip().upper() != "ALL":
                permitidas = [o.strip().lower() for o in obras_raw.split(",") if o.strip()]
                if proyecto.strip().lower() not in permitidas:
                    raise HTTPException(status_code=403, detail=f"No tienes autorización para asignar tareas en la obra '{proyecto}'.")

    asignar_actividad_empleados(
        empleados_nombres=empleados,
        proyecto_nombre=proyecto,
        piso=piso,
        actividad=actividad,
        asignado_por=asignado_por,
        hora_personalizada=hora,
        notas=notas
    )

    try:
        from .webpush_service import enviar_webpush_async
        enviar_webpush_async(
            empleados=empleados,
            titulo="🔨 Nueva Labor Asignada",
            mensaje=f"📍 {piso} • {actividad} ({proyecto})"
        )
    except Exception:
        pass

    return {
        "status": "ok",
        "total_asignados": len(empleados),
        "mensaje": f"Tarea asignada exitosamente a {len(empleados)} trabajador(es)."
    }


@app.get("/api/reporte_actividades")
def api_obtener_reporte_actividades(
    fecha: Optional[str] = None,
    proyecto: Optional[str] = "TODOS"
):
    filas = obtener_reporte_actividades_costos(fecha_inicio=fecha, proyecto_nombre=proyecto)
    return {"status": "ok", "total": len(filas), "actividades": filas}


@app.get("/api/reporte_actividades_csv")
def api_reporte_actividades_csv(
    fecha: Optional[str] = None,
    proyecto: Optional[str] = "TODOS"
):
    filas = obtener_reporte_actividades_costos(fecha_inicio=fecha, proyecto_nombre=proyecto)
    encabezados = [
        "ID Registro",
        "Fecha",
        "Nombre del Trabajador",
        "Obra / Proyecto",
        "Piso Asignado",
        "Actividad Ejecutada",
        "Hora Inicio Tarea",
        "Hora Fin Tarea",
        "Horas Dedicadas",
        "Asignado Por",
        "Hora Entrada Obra",
        "Hora Salida Obra"
    ]
    csv_text = ";".join(encabezados) + "\r\n"
    for f in filas:
        row = [
            str(f["id"]),
            f'"{f["fecha"]}"',
            f'"{f["empleado_nombre"]}"',
            f'"{f["proyecto_nombre"]}"',
            f'"{f["piso"]}"',
            f'"{f["actividad"]}"',
            f'"{f["hora_inicio"]}"',
            f'"{f["hora_fin"]}"',
            str(f["horas_dedicadas"]).replace(".", ","),
            f'"{f["asignado_por"]}"',
            f'"{f["entrada_general"]}"',
            f'"{f["salida_general"]}"'
        ]
        csv_text += ";".join(row) + "\r\n"

    buffer = io.BytesIO(("\ufeff" + csv_text).encode("utf-8"))
    filename = f"Actividades_y_Costos_RyanPacific_{fecha or datetime.now().strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/actividades/record_dia")
def api_record_actividades_empleado(
    empleado: str = Query(...),
    fecha: str = Query(...)
):
    """Retorna la lista cronológica de actividades realizadas por un trabajador en una fecha."""
    actividades = obtener_historial_actividades_empleado_dia(empleado_nombre=empleado, fecha=fecha)
    return {"status": "ok", "total": len(actividades), "actividades": actividades}


# ---------------- API DE COMUNICADOS OFICIALES Y ALERTAS PUSH ----------------

@app.post("/api/comunicados")
def api_crear_comunicado(payload: dict):
    """Crea y despacha un comunicado a toda la empresa, a una obra o a obreros específicos."""
    titulo = str(payload.get("titulo", "")).strip()
    mensaje = str(payload.get("mensaje", "")).strip()
    tipo_entrega = str(payload.get("tipo_entrega") or payload.get("tipo_alerta") or "IN_APP").upper()
    alcance = str(payload.get("alcance") or payload.get("target_tipo") or "TODOS").upper()
    destinos = payload.get("destinos") if payload.get("destinos") is not None else payload.get("target_valor", [])
    if isinstance(destinos, str):
        try:
            destinos = json.loads(destinos)
        except Exception:
            destinos = [destinos] if destinos else []

    if not titulo or not mensaje:
        raise HTTPException(status_code=400, detail="Título y mensaje son obligatorios.")
    res = crear_comunicado(titulo, mensaje, tipo_entrega, alcance, destinos)

    try:
        from .webpush_service import enviar_webpush_async
        dest_push = destinos if str(alcance).upper() in ("INDIVIDUAL", "PERSONAS", "USUARIOS") else None
        enviar_webpush_async(
            empleados=dest_push,
            titulo=f"📢 {titulo}",
            mensaje=mensaje,
            url="/"
        )
    except Exception as e_wp:
        logger.warning(f"Aviso al enviar push: {e_wp}")

    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return res


@app.get("/api/comunicados")
def api_listar_comunicados():
    """Listado de comunicados enviados con auditoría de quién lo leyó en vivo."""
    return {"status": "ok", "comunicados": listar_comunicados_admin()}


@app.delete("/api/comunicados/{comunicado_id}")
def api_eliminar_comunicado(comunicado_id: int):
    """Elimina un comunicado para que no siga apareciendo en los celulares de los trabajadores."""
    res = eliminar_comunicado(comunicado_id)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return res


@app.put("/api/comunicados/{comunicado_id}")
def api_actualizar_comunicado(comunicado_id: int, payload: dict):
    """Permite modificar el título, texto, tipo de alerta o destinatarios de un comunicado emitido."""
    titulo = str(payload.get("titulo", "")).strip()
    mensaje = str(payload.get("mensaje", "")).strip()
    tipo_entrega = str(payload.get("tipo_entrega") or payload.get("tipo_alerta") or "IN_APP").upper()
    alcance = str(payload.get("alcance") or payload.get("target_tipo") or "TODOS").upper()
    destinos = payload.get("destinos") if payload.get("destinos") is not None else payload.get("target_valor", [])
    if isinstance(destinos, str):
        try:
            destinos = json.loads(destinos)
        except Exception:
            destinos = [destinos] if destinos else []

    if not titulo or not mensaje:
        raise HTTPException(status_code=400, detail="Título y mensaje son obligatorios.")

    res = actualizar_comunicado(comunicado_id, titulo, mensaje, tipo_entrega, alcance, destinos)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return res


@app.get("/api/comunicados/pendientes")
def api_comunicados_pendientes(nombre: str = Query(...), obra: Optional[str] = Query(None)):
    """Verifica si el trabajador tiene avisos o comunicados pendientes de confirmar."""
    pendientes = obtener_comunicados_pendientes_empleado(nombre, obra)
    return {"status": "ok", "pendientes": pendientes, "comunicados": pendientes}


@app.get("/api/empleado/mis_avisos_y_tareas")
def api_mis_avisos_y_tareas(nombre: str = Query(...), obra: Optional[str] = Query(None)):
    """Retorna la tarea asignada hoy y el historial completo de comunicados para consulta del trabajador."""
    return obtener_mis_tareas_y_comunicados(nombre, obra)


@app.post("/api/comunicados/{comunicado_id}/confirmar_lectura")
def api_confirmar_lectura_comunicado(comunicado_id: int, payload: dict):
    """Registra la firma digital obligatoria de 'Entendido y Recibido'."""
    nombre = str(payload.get("nombre") or payload.get("empleado_nombre") or "").strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="Nombre del trabajador requerido.")
    res = registrar_lectura_comunicado(comunicado_id, nombre)
    try:
        from .gdrive import sincronizar_db_a_drive_master_async
        sincronizar_db_a_drive_master_async()
    except Exception:
        pass
    return res


@app.get("/api/push/vapid_public_key")
def api_obtener_vapid_key():
    """Retorna la clave pública VAPID para que el celular suscriba el ServiceWorker a WebPush."""
    from .webpush_service import obtener_vapid_public_key
    return {"status": "ok", "public_key": obtener_vapid_public_key()}


@app.post("/api/push/subscribe")
def api_push_subscribe(payload: dict):
    """Registra la suscripción Web Push del celular para alertas de pantalla bloqueada."""
    nombre = str(payload.get("nombre") or payload.get("empleado_nombre") or "").strip()
    sub = payload.get("subscription") or payload.get("subscription_info")
    if isinstance(sub, dict):
        sub_str = json.dumps(sub)
    elif isinstance(sub, str):
        sub_str = sub
    else:
        sub_str = ""
    if not nombre or not sub_str:
        return {"status": "error", "error": "Datos incompletos"}
    res = guardar_suscripcion_push(nombre, sub_str)
    return res


# Servir fotos de capturas y de perfiles oficiales con caché HTTP ultra-rápida (30 días)
CACHE_HEADERS_FOTOS = {"Cache-Control": "public, max-age=2592000, immutable"}

@app.get("/data/capturas/{nombre_archivo}")
def servir_captura(nombre_archivo: str):
    ruta = os.path.join(CAPTURAS_DIR, nombre_archivo)
    if os.path.exists(ruta):
        return FileResponse(ruta, headers=CACHE_HEADERS_FOTOS)
    
    # Intentar descargar de Google Drive si falta en disco efímero de Render
    try:
        from .gdrive import descargar_foto_desde_drive
        descargado = descargar_foto_desde_drive(nombre_archivo, ruta)
        if descargado and os.path.exists(ruta):
            return FileResponse(ruta, headers=CACHE_HEADERS_FOTOS)
    except Exception as e:
        logger.warning(f"No se pudo descargar captura {nombre_archivo} de Drive: {e}")

    # Retornar 404 para que el frontend dispare onerror y muestre 'Sin foto' (nunca el logo)
    raise HTTPException(status_code=404, detail="Foto de marcaje no encontrada")


@app.get("/data/perfiles/{nombre_archivo}")
def servir_perfil(nombre_archivo: str):
    ruta = os.path.join(PERFILES_DIR, nombre_archivo)
    if os.path.exists(ruta):
        return FileResponse(ruta, headers=CACHE_HEADERS_FOTOS)
    
    # Intentar descargar de Google Drive si falta en disco efímero de Render
    try:
        from .gdrive import descargar_foto_desde_drive
        descargado = descargar_foto_desde_drive(nombre_archivo, ruta)
        if descargado and os.path.exists(ruta):
            return FileResponse(ruta, headers=CACHE_HEADERS_FOTOS)
    except Exception as e:
        logger.warning(f"No se pudo descargar perfil {nombre_archivo} de Drive: {e}")

    # Retornar avatar neutral SVG limpio (nunca recuadro negro ni logo empresarial)
    avatar_svg = os.path.join(STATIC_DIR, "avatar_default.svg")
    if os.path.exists(avatar_svg):
        return FileResponse(avatar_svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404, detail="Foto de perfil no encontrada")


# Endpoints de Respaldo en Google Drive
@app.post("/api/gdrive/respaldar")
def api_gdrive_respaldar():
    """Ejecuta un respaldo inmediato de la base de datos sqlite en Google Drive."""
    try:
        from backend.gdrive import realizar_respaldo_completo
        res = realizar_respaldo_completo()
        return res
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/gdrive/restaurar")
def api_gdrive_restaurar():
    """Fuerza la descarga y restauración inmediata de la base de datos maestra desde Google Drive."""
    try:
        from backend.gdrive import restaurar_db_desde_drive_master
        exito = restaurar_db_desde_drive_master()
        if exito:
            init_db()
            return {"status": "ok", "mensaje": "Base de datos maestra restaurada exitosamente desde Google Drive."}
        else:
            return {"status": "error", "error": "No se encontró 'asistencia_master.db' en Google Drive o falló la conexión."}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/gdrive/status")
def api_gdrive_status():
    """Verifica si Google Drive está conectado correctamente."""
    try:
        from backend.gdrive import obtener_servicio_drive, GDRIVE_FOLDER_ID
        srv = obtener_servicio_drive()
        return {
            "status": "ok" if srv else "error",
            "conectado": srv is not None,
            "carpeta_id": GDRIVE_FOLDER_ID
        }
    except Exception as e:
        return {"status": "error", "conectado": False, "error": str(e)}
