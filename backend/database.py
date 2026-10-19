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

    # Tabla de Proyectos / Obras
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS proyectos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        radio_metros INTEGER NOT NULL DEFAULT 150,
        qr_token TEXT NOT NULL UNIQUE,
        activo INTEGER DEFAULT 1
    )
    """)

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
    SELECT id, nombre, telefono, puesto, foto_perfil, fecha_registro, activo 
    FROM empleados 
    WHERE activo = 1 
    ORDER BY nombre ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

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

def guardar_proyecto(nombre, lat, lon, radio_metros, qr_token):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO proyectos (nombre, lat, lon, radio_metros, qr_token)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(nombre) DO UPDATE SET
        lat = excluded.lat,
        lon = excluded.lon,
        radio_metros = excluded.radio_metros,
        qr_token = excluded.qr_token
    """, (nombre, lat, lon, radio_metros, qr_token))
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
