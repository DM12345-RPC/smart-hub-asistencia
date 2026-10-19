import uvicorn
import os
import sys

# Asegurar que el directorio raíz del proyecto esté en el path de Python
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

if __name__ == "__main__":
    # Render inyecta la variable de entorno PORT (por defecto 10000)
    port = int(os.environ.get("PORT", 10000))
    
    print("=======================================================")
    print("🚀 INICIANDO SMART HUB - ASISTENCIA RYAN PACIFIC")
    print("=======================================================")
    print(f"📡 Puerto activo: {port}")
    print(f"📱 App Móvil Obrero (PWA) : http://localhost:{port}/")
    print(f"🖥️  Panel Administrativo  : http://localhost:{port}/admin")
    print("=======================================================")
    
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        workers=1
    )
