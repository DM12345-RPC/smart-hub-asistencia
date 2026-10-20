import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "asistencia.db")

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Tabla de Empleados
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS empleados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE,
        telefono TEXT,
        puesto TEXT DEFAULT 'Obrero / Campo',
        foto_perfil TEXT, -- Ruta a la foto oficial de enrolamiento
        vectores_json TEXT NOT NULL, -- Lista de embeddings normalizados
        fecha_registro TEXT NOT NULL,
        activo INTEGER DEFAULT 1
    )
    """)

    # Migración de columnas si la tabla ya existía
    cursor.execute("PRAGMA table_info(empleados)")
    cols_emp = [r["name"] for r in cursor.fetchall()]
    if "telefono" not in cols_emp:
        cursor.execute("ALTER TABLE empleados ADD COLUMN telefono TEXT")
    if "foto_perfil" not in cols_emp:
        cursor.execute("ALTER TABLE empleados ADD COLUMN foto_perfil TEXT")
    if "rol" not in cols_emp:
        cursor.execute("ALTER TABLE empleados ADD COLUMN rol TEXT DEFAULT 'worker'")

    # Tabla de Proyectos / Obras
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proyectos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        radio_metros INTEGER NOT NULL DEFAULT 150,
        qr_token TEXT NOT NULL UNIQUE,
        activo INTEGER DEFAULT 1,
        pisos TEXT DEFAULT '',
        actividades TEXT DEFAULT ''
    )
    """)

    # Migración de columnas de proyectos si no existen
    cursor.execute("PRAGMA table_info(proyectos)")
    cols_proy = [r["name"] for r in cursor.fetchall()]
    if "pisos" not in cols_proy:
        cursor.execute("ALTER TABLE proyectos ADD COLUMN pisos TEXT DEFAULT ''")
    if "actividades" not in cols_proy:
        cursor.execute("ALTER TABLE proyectos ADD COLUMN actividades TEXT DEFAULT ''")

    # Asegurar que las obras existentes tengan valores iniciales de pisos y actividades si están vacías
    default_pisos = "Sótano 1, Piso 1, Piso 2, Piso 3, Azotea"
    default_actividades = "Encofrado, Fierrería / Acero, Vaciado de Concreto, Drywall, Pintura, Limpieza General"
    cursor.execute("""
        UPDATE proyectos 
        SET pisos = ? 
        WHERE pisos IS NULL OR pisos = ''
    """, (default_pisos,))
    cursor.execute("""
        UPDATE proyectos 
        SET actividades = ? 
        WHERE actividades IS NULL OR actividades = ''
    """, (default_actividades,))

    # Tabla de Marcajes de Asistencia
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS marcajes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_nombre TEXT NOT NULL,
        tipo TEXT NOT NULL, -- ENTRADA / SALIDA
        timestamp_local TEXT NOT NULL, -- Hora que marcó el celular
        timestamp_servidor TEXT NOT NULL, -- Hora que llegó al servidor
        proyecto_nombre TEXT NOT NULL,
        lat REAL,
        lon REAL,
        distancia_metros REAL,
        gps_accuracy REAL,
        metodo_ubicacion TEXT, -- GPS, QR_OBRA, DESCONOCIDO
        similitud_facial REAL,
        estado_auditoria TEXT NOT NULL, -- APROBADO, REVISION_GPS, REVISION_FACIAL, SOSPECHA_FRAUDE
        foto_path TEXT,
        offline_sincronizado INTEGER DEFAULT 0,
        notas TEXT
    )
    """)

    # Tabla de Actividades Diarias (Asignación de Pisos y Tareas por Foreman)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS actividades_diarias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        empleado_nombre TEXT NOT NULL,
        proyecto_nombre TEXT NOT NULL,
        piso TEXT NOT NULL,
        actividad TEXT NOT NULL,
        hora_inicio TEXT NOT NULL,
        hora_fin TEXT,
        asignado_por TEXT DEFAULT 'Foreman',
        notas TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_actividades_fecha_emp ON actividades_diarias(fecha, empleado_nombre)")

    # Insertar proyectos iniciales si está vacío
    cursor.execute("SELECT COUNT(*) as count FROM proyectos")
    if cursor.fetchone()["count"] == 0:
        proyectos_iniciales = [
            ("Obra Ryan Pacific - Sede Principal", 34.0522, -118.2437, 200, "RPCS-OBRA-01-SEDE-X9"),
            ("Proyecto Los Ángeles - Estructuras", 34.0407, -118.2468, 150, "RPCS-OBRA-02-ESTR-K2"),
            ("Proyecto Costa Oeste - Excavación", 34.0195, -118.4912, 250, "RPCS-OBRA-03-EXCA-M7")
        ]
        cursor.executemany("""
        INSERT INTO proyectos (nombre, lat, lon, radio_metros, qr_token)
        VALUES (?, ?, ?, ?, ?)
        """, proyectos_iniciales)

    conn.commit()
    conn.close()

# ----------------- OPERACIONES DE EMPLEADOS -----------------

def guardar_o_actualizar_empleado(nombre, telefono, puesto, nuevos_vectores, foto_perfil=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, vectores_json, foto_perfil FROM empleados WHERE nombre = ?", (nombre,))
    row = cursor.fetchone()

    if row:
        vectores_existentes = json.loads(row["vectores_json"])
        vectores_existentes.extend(nuevos_vectores)
        if len(vectores_existentes) > 5:
            vectores_existentes = vectores_existentes[-5:]
        
        foto_final = foto_perfil if foto_perfil else row["foto_perfil"]
        cursor.execute("""
        UPDATE empleados 
        SET vectores_json = ?, puesto = ?, telefono = ?, foto_perfil = ? 
        WHERE id = ?
        """, (json.dumps(vectores_existentes), puesto, telefono, foto_final, row["id"]))
    else:
        cursor.execute("""
        INSERT INTO empleados (nombre, telefono, puesto, foto_perfil, vectores_json, fecha_registro)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (nombre, telefono, puesto, foto_perfil, json.dumps(nuevos_vectores), datetime.now().isoformat()))

    conn.commit()
    conn.close()

def obtener_empleado(nombre):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM empleados WHERE nombre = ? AND activo = 1", (nombre,))
    row = cursor.fetchone()
    conn.close()
    if row:
        d = dict(row)
        d["vectores"] = json.loads(d["vectores_json"])
        return d
    return None

def listar_empleados():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, nombre, telefono, puesto, COALESCE(rol, 'worker') as rol, foto_perfil, fecha_registro, activo 
    FROM empleados 
    WHERE activo = 1 
    ORDER BY nombre ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def actualizar_rol_empleado(empleado_id, nuevo_rol):
    if nuevo_rol not in ('worker', 'foreman', 'admin'):
        nuevo_rol = 'worker'
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE empleados SET rol = ? WHERE id = ?", (nuevo_rol, empleado_id))
    conn.commit()
    conn.close()
    return True

def obtener_rol_empleado(nombre):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT rol FROM empleados WHERE nombre = ? AND activo = 1", (nombre,))
    row = cursor.fetchone()
    conn.close()
    if row and row["rol"]:
        return row["rol"]
    return "worker"

def eliminar_empleado(empleado_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM empleados WHERE id = ?", (empleado_id,))
    conn.commit()
    conn.close()

# ----------------- OPERACIONES DE PROYECTOS -----------------

def listar_proyectos():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM proyectos WHERE activo = 1 ORDER BY nombre ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def obtener_proyecto_por_nombre(nombre):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM proyectos WHERE nombre = ?", (nombre,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def obtener_proyecto_por_qr(qr_token):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM proyectos WHERE qr_token = ?", (qr_token.strip(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def guardar_proyecto(nombre, lat, lon, radio_metros, qr_token, pisos="", actividades=""):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO proyectos (nombre, lat, lon, radio_metros, qr_token, pisos, actividades)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(nombre) DO UPDATE SET
        lat = excluded.lat,
        lon = excluded.lon,
        radio_metros = excluded.radio_metros,
        qr_token = excluded.qr_token,
        pisos = CASE WHEN excluded.pisos != '' THEN excluded.pisos ELSE proyectos.pisos END,
        actividades = CASE WHEN excluded.actividades != '' THEN excluded.actividades ELSE proyectos.actividades END
    """, (nombre, lat, lon, radio_metros, qr_token, pisos, actividades))
    conn.commit()
    conn.close()

def actualizar_config_proyecto(proyecto_id, pisos, actividades):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE proyectos
        SET pisos = ?, actividades = ?
        WHERE id = ?
    """, (pisos, actividades, proyecto_id))
    conn.commit()
    conn.close()

def eliminar_proyecto(proyecto_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proyectos WHERE id = ?", (proyecto_id,))
    conn.commit()
    conn.close()

# ----------------- OPERACIONES DE MARCAJES -----------------

def registrar_marcaje(datos):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO marcajes (
        empleado_nombre, tipo, timestamp_local, timestamp_servidor,
        proyecto_nombre, lat, lon, distancia_metros, gps_accuracy,
        metodo_ubicacion, similitud_facial, estado_auditoria,
        foto_path, offline_sincronizado, notas
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datos["empleado_nombre"],
        datos["tipo"],
        datos["timestamp_local"],
        datos.get("timestamp_servidor", datetime.now().isoformat()),
        datos["proyecto_nombre"],
        datos.get("lat"),
        datos.get("lon"),
        datos.get("distancia_metros"),
        datos.get("gps_accuracy"),
        datos.get("metodo_ubicacion", "GPS"),
        datos.get("similitud_facial", 0.0),
        datos.get("estado_auditoria", "REVISION"),
        datos.get("foto_path"),
        datos.get("offline_sincronizado", 0),
        datos.get("notas", "")
    ))
    nuevo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return nuevo_id

def listar_marcajes(limit=100, filtro_estado=None, filtro_fecha=None):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM marcajes WHERE 1=1"
    params = []

    if filtro_estado and filtro_estado != "TODOS":
        query += " AND estado_auditoria = ?"
        params.append(filtro_estado)

    if filtro_fecha:
        query += " AND timestamp_local LIKE ?"
        params.append(f"{filtro_fecha}%")

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    marcajes = [dict(r) for r in rows]

    if marcajes:
        # Obtener la actividad actual (última del día) para cada trabajador
        cursor.execute("""
            SELECT id, fecha, empleado_nombre, piso, actividad, hora_inicio, hora_fin
            FROM actividades_diarias
            ORDER BY id ASC
        """)
        acts = cursor.fetchall()
        acts_map = {}
        for a in acts:
            k = (a["fecha"], a["empleado_nombre"])
            acts_map[k] = {
                "piso": a["piso"],
                "actividad": a["actividad"],
                "hora_inicio": a["hora_inicio"],
                "hora_fin": a["hora_fin"]
            }

        for m in marcajes:
            fecha_m = (m.get("timestamp_local") or "")[:10]
            emp_m = m.get("empleado_nombre")
            m["actividad_actual"] = acts_map.get((fecha_m, emp_m))

    conn.close()
    return marcajes

def obtener_historial_actividades_empleado_dia(empleado_nombre, fecha):
    """Retorna todas las actividades que una persona ejecutó en un día específico."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, fecha, empleado_nombre, proyecto_nombre, piso, actividad, hora_inicio, hora_fin, asignado_por
        FROM actividades_diarias
        WHERE empleado_nombre = ? AND fecha = ?
        ORDER BY id ASC
    """, (empleado_nombre, fecha))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def eliminar_marcaje(marcaje_id):
    conn = get_connection()
    cursor = conn.cursor()
    # Obtener ruta de foto si existe para borrarla del disco
    cursor.execute("SELECT foto_path FROM marcajes WHERE id = ?", (marcaje_id,))
    row = cursor.fetchone()
    if row and row["foto_path"]:
        try:
            ruta_disco = os.path.join(os.path.dirname(__file__), "..", row["foto_path"].lstrip("/"))
            if os.path.exists(ruta_disco):
                os.remove(ruta_disco)
        except Exception:
            pass

    cursor.execute("DELETE FROM marcajes WHERE id = ?", (marcaje_id,))
    conn.commit()
    conn.close()

def obtener_metricas_dashboard():
    conn = get_connection()
    cursor = conn.cursor()
    hoy = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT COUNT(*) as total FROM marcajes WHERE timestamp_local LIKE ?", (f"{hoy}%",))
    total_hoy = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as aprobados FROM marcajes WHERE timestamp_local LIKE ? AND estado_auditoria = 'APROBADO'", (f"{hoy}%",))
    aprobados = cursor.fetchone()["aprobados"]

    cursor.execute("SELECT COUNT(*) as fraudes FROM marcajes WHERE timestamp_local LIKE ? AND estado_auditoria = 'SOSPECHA_FRAUDE'", (f"{hoy}%",))
    fraudes = cursor.fetchone()["fraudes"]

    cursor.execute("SELECT COUNT(*) as qr_count FROM marcajes WHERE timestamp_local LIKE ? AND metodo_ubicacion = 'QR_OBRA'", (f"{hoy}%",))
    marcajes_qr = cursor.fetchone()["qr_count"]

    cursor.execute("SELECT COUNT(DISTINCT empleado_nombre) as activos FROM marcajes WHERE timestamp_local LIKE ? AND tipo = 'ENTRADA'", (f"{hoy}%",))
    trabajadores_activos = cursor.fetchone()["activos"]

    conn.close()
    return {
        "fecha": hoy,
        "total_marcajes_hoy": total_hoy,
        "aprobados_hoy": aprobados,
        "alertas_fraude_hoy": fraudes,
        "marcajes_bajo_losa_qr": marcajes_qr,
        "trabajadores_activos_hoy": trabajadores_activos
    }

def obtener_resumen_dia_foreman():
    """
    Genera el resumen en tiempo real del personal en cada obra para el Foreman y Admin.
    Calcula activos (último marcaje hoy = ENTRADA) vs salidos (último marcaje hoy = SALIDA).
    """
    conn = get_connection()
    cursor = conn.cursor()
    hoy = datetime.now().strftime("%Y-%m-%d")

    # Obtener el último marcaje de cada trabajador hoy
    query = """
    WITH ultimos AS (
        SELECT 
            m.empleado_nombre,
            m.tipo,
            m.proyecto_nombre,
            m.timestamp_local,
            ROW_NUMBER() OVER (
                PARTITION BY m.empleado_nombre 
                ORDER BY m.id DESC
            ) as rn
        FROM marcajes m
        WHERE m.timestamp_local LIKE ? OR m.timestamp_servidor LIKE ?
    )
    SELECT 
        empleado_nombre,
        tipo,
        proyecto_nombre,
        timestamp_local
    FROM ultimos
    WHERE rn = 1
    ORDER BY proyecto_nombre ASC, empleado_nombre ASC
    """
    cursor.execute(query, (f"{hoy}%", f"{hoy}%"))
    filas = cursor.fetchall()

    # Proyectos registrados
    cursor.execute("SELECT nombre FROM proyectos WHERE activo = 1 ORDER BY nombre ASC")
    proyectos = [p["nombre"] for p in cursor.fetchall()]

    # Actividades asignadas hoy agrupadas por empleado
    cursor.execute("""
        SELECT id, fecha, empleado_nombre, proyecto_nombre, piso, actividad, hora_inicio, hora_fin, asignado_por
        FROM actividades_diarias
        WHERE fecha = ?
        ORDER BY id ASC
    """, (hoy,))
    actividades_hoy = cursor.fetchall()
    conn.close()

    acts_por_emp = {}
    for a in actividades_hoy:
        emp = a["empleado_nombre"]
        if emp not in acts_por_emp:
            acts_por_emp[emp] = []
        acts_por_emp[emp].append({
            "id": a["id"],
            "piso": a["piso"],
            "actividad": a["actividad"],
            "hora_inicio": a["hora_inicio"],
            "hora_fin": a["hora_fin"],
            "asignado_por": a["asignado_por"]
        })

    obras_dict = {}
    for p in proyectos:
        obras_dict[p] = {"nombre": p, "activos": 0, "total": 0, "trabajadores": []}

    gran_total_activos = 0
    gran_total_general = 0

    for f in filas:
        p_nom = f["proyecto_nombre"] or "Sin Obra Asignada"
        if p_nom not in obras_dict:
            obras_dict[p_nom] = {"nombre": p_nom, "activos": 0, "total": 0, "trabajadores": []}

        es_activo = (f["tipo"].upper() == "ENTRADA")
        estado_label = "ACTIVO" if es_activo else "SALIÓ"
        if es_activo:
            obras_dict[p_nom]["activos"] += 1
            gran_total_activos += 1

        obras_dict[p_nom]["total"] += 1
        gran_total_general += 1

        emp_acts = acts_por_emp.get(f["empleado_nombre"], [])
        act_actual = emp_acts[-1] if emp_acts else None

        obras_dict[p_nom]["trabajadores"].append({
            "nombre": f["empleado_nombre"],
            "estado": estado_label,
            "actividad_actual": act_actual,
            "total_actividades": len(emp_acts),
            "actividades": emp_acts
        })

    # Filtrar solo obras con trabajadores hoy (o todas si no hay ninguno)
    obras_lista = [v for v in obras_dict.values() if v["total"] > 0]
    if not obras_lista:
        obras_lista = [v for v in obras_dict.values()]

    return {
        "status": "ok",
        "fecha": hoy,
        "gran_total_activos": gran_total_activos,
        "gran_total_general": gran_total_general,
        "obras": obras_lista
    }

# ----------------- OPERACIONES DE ACTIVIDADES EN OBRA -----------------

def asignar_actividad_empleados(empleados_nombres, proyecto_nombre, piso, actividad, asignado_por="Foreman", hora_personalizada=None, notas=""):
    """
    Asigna piso y actividad a uno o varios trabajadores (cuadrilla).
    Cierra automáticamente la tarea anterior del día colocando su hora_fin.
    """
    conn = get_connection()
    cursor = conn.cursor()
    ahora = datetime.now()
    fecha_hoy = ahora.strftime("%Y-%m-%d")
    hora_str = hora_personalizada or ahora.strftime("%H:%M:%S")

    for emp in empleados_nombres:
        emp_clean = emp.strip()
        if not emp_clean:
            continue
        
        # 1. Cerrar tarea anterior abierta de hoy
        cursor.execute("""
            UPDATE actividades_diarias
            SET hora_fin = ?
            WHERE fecha = ? AND empleado_nombre = ? AND (hora_fin IS NULL OR hora_fin = '')
        """, (hora_str, fecha_hoy, emp_clean))

        # 2. Insertar nueva actividad
        cursor.execute("""
            INSERT INTO actividades_diarias (
                fecha, empleado_nombre, proyecto_nombre, piso, actividad,
                hora_inicio, hora_fin, asignado_por, notas
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """, (fecha_hoy, emp_clean, proyecto_nombre, piso, actividad, hora_str, asignado_por, notas))

    conn.commit()
    conn.close()
    return True

def cerrar_actividades_abiertas_empleado(empleado_nombre, hora_cierre=None, fecha=None):
    """
    Al registrar SALIDA, cierra la última tarea que el trabajador tenía abierta en el día.
    """
    conn = get_connection()
    cursor = conn.cursor()
    ahora = datetime.now()
    fecha_hoy = fecha or ahora.strftime("%Y-%m-%d")
    
    if hora_cierre:
        if "T" in str(hora_cierre):
            hora_str = str(hora_cierre).split("T")[1][:8]
        elif " " in str(hora_cierre):
            hora_str = str(hora_cierre).split(" ")[1][:8]
        else:
            hora_str = str(hora_cierre)[:8]
    else:
        hora_str = ahora.strftime("%H:%M:%S")

    cursor.execute("""
        UPDATE actividades_diarias
        SET hora_fin = ?
        WHERE fecha = ? AND empleado_nombre = ? AND (hora_fin IS NULL OR hora_fin = '')
    """, (hora_str, fecha_hoy, empleado_nombre))
    conn.commit()
    conn.close()

def calcular_horas_entre(hora_inicio, hora_fin):
    try:
        h1 = hora_inicio.strip()[:8]
        h2 = hora_fin.strip()[:8]
        fmt1 = "%H:%M:%S" if len(h1) == 8 else "%H:%M"
        fmt2 = "%H:%M:%S" if len(h2) == 8 else "%H:%M"
        t1 = datetime.strptime(h1, fmt1)
        t2 = datetime.strptime(h2, fmt2)
        diff = (t2 - t1).total_seconds() / 3600.0
        return round(max(0.0, diff), 2)
    except Exception:
        return 0.0

def obtener_reporte_actividades_costos(fecha_inicio=None, fecha_fin=None, proyecto_nombre=None):
    """
    Genera el reporte detallado donde CADA actividad realizada por una persona
    es UNA FILA independiente con su Piso, Actividad, Hora Inicio, Hora Fin y Duración en Horas.
    """
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT 
            a.id,
            a.fecha,
            a.empleado_nombre,
            a.proyecto_nombre,
            a.piso,
            a.actividad,
            a.hora_inicio,
            a.hora_fin,
            a.asignado_por,
            a.notas
        FROM actividades_diarias a
        WHERE 1=1
    """
    params = []
    if fecha_inicio and fecha_fin:
        query += " AND a.fecha BETWEEN ? AND ?"
        params.extend([fecha_inicio, fecha_fin])
    elif fecha_inicio:
        query += " AND a.fecha = ?"
        params.append(fecha_inicio)

    if proyecto_nombre and proyecto_nombre != "TODOS":
        query += " AND a.proyecto_nombre = ?"
        params.append(proyecto_nombre)

    query += " ORDER BY a.fecha DESC, a.proyecto_nombre ASC, a.empleado_nombre ASC, a.id ASC"
    cursor.execute(query, params)
    filas = cursor.fetchall()

    # Obtener entradas y salidas generales de marcajes para cruzar
    cursor.execute("""
        SELECT empleado_nombre, tipo, timestamp_local
        FROM marcajes
        ORDER BY id ASC
    """)
    marcajes = cursor.fetchall()
    conn.close()

    horarios_obra = {}
    for m in marcajes:
        try:
            f_str = m["timestamp_local"][:10]
            h_str = m["timestamp_local"][11:19]
            clave = (f_str, m["empleado_nombre"])
            if clave not in horarios_obra:
                horarios_obra[clave] = {"entrada": None, "salida": None}
            if m["tipo"] == "ENTRADA" and not horarios_obra[clave]["entrada"]:
                horarios_obra[clave]["entrada"] = h_str
            elif m["tipo"] == "SALIDA":
                horarios_obra[clave]["salida"] = h_str
        except Exception:
            pass

    reporte = []
    ahora_hora = datetime.now().strftime("%H:%M:%S")
    hoy_str = datetime.now().strftime("%Y-%m-%d")

    for f in filas:
        h_ini = f["hora_inicio"]
        h_fin = f["hora_fin"]
        horas_calc = 0.0

        if h_fin:
            horas_calc = calcular_horas_entre(h_ini, h_fin)
            h_fin_str = h_fin
        elif f["fecha"] == hoy_str:
            horas_calc = calcular_horas_entre(h_ini, ahora_hora)
            h_fin_str = f"{ahora_hora} (En curso)"
        else:
            h_fin_str = "Sin cierre"

        clave_emp = (f["fecha"], f["empleado_nombre"])
        info_obra = horarios_obra.get(clave_emp, {"entrada": "N/R", "salida": "N/R"})

        reporte.append({
            "id": f["id"],
            "fecha": f["fecha"],
            "empleado_nombre": f["empleado_nombre"],
            "proyecto_nombre": f["proyecto_nombre"],
            "piso": f["piso"],
            "actividad": f["actividad"],
            "hora_inicio": h_ini,
            "hora_fin": h_fin_str,
            "horas_dedicadas": horas_calc,
            "asignado_por": f["asignado_por"] or "Foreman",
            "entrada_general": info_obra.get("entrada") or "N/R",
            "salida_general": info_obra.get("salida") or "N/R"
        })

    return reporte
