import math

def calcular_distancia_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcula la distancia en metros entre dos coordenadas geográficas
    utilizando la fórmula del semiverseno (Haversine).
    """
    R = 6371000.0  # Radio medio de la Tierra en metros
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return round(R * c, 2)

def validar_posicion_en_obra(lat_obrero, lon_obrero, accuracy, proy_lat, proy_lon, radio_max_metros):
    """
    Evalúa si la coordenada del obrero cumple los criterios de geocerca de la obra.
    Retorna: (es_valido: bool, distancia: float, motivo: str)
    """
    if lat_obrero is None or lon_obrero is None:
        return False, None, "GPS no disponible"

    distancia = calcular_distancia_haversine(lat_obrero, lon_obrero, proy_lat, proy_lon)

    # Verificación de precisión: si la precisión es mayor a 120m, la señal rebotó o es triangulación celular
    if accuracy is not None and accuracy > 120:
        return False, distancia, f"Precisión GPS insuficiente (±{int(accuracy)}m)"

    if distancia <= radio_max_metros:
        return True, distancia, "Dentro del perímetro de la obra"
    else:
        dist_km = round(distancia / 1000, 2)
        if dist_km >= 1.0:
            return False, distancia, f"Fuera de rango: A {dist_km} km de la obra (Máx: {radio_max_metros}m)"
        else:
            return False, distancia, f"Fuera de rango: A {int(distancia)}m de la obra (Máx: {radio_max_metros}m)"
