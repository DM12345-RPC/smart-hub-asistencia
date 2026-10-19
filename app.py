import os
import sys
import uvicorn

# Asegurar que el directorio raíz esté en sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.app import app

# Integración nativa con Gradio para Hugging Face Spaces (SDK: Gradio)
try:
    import gradio as gr
    
    with gr.Blocks(title="Smart Hub Asistencia Ryan Pacific") as demo:
        gr.HTML("""
        <div style="text-align:center; padding: 40px; font-family: sans-serif;">
            <h2>⏱️ Smart Hub - Asistencia Ryan Pacific</h2>
            <p>La aplicación está activa en este servidor.</p>
            <div style="margin: 20px 0;">
                <a href="/" target="_blank" style="background:#16a34a; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:bold; margin-right:10px;">
                    📱 Abrir App del Obrero
                </a>
                <a href="/admin" target="_blank" style="background:#1e293b; color:white; padding:12px 24px; border-radius:8px; text-decoration:none; font-weight:bold;">
                    🖥️ Abrir Panel Administrativo
                </a>
            </div>
            <script>
                // Si se abre en pantalla completa, redirigir automáticamente al frontend
                if (window.self === window.top) {
                    window.location.href = '/';
                }
            </script>
        </div>
        """)

    # Montar Gradio sobre la aplicación FastAPI principal
    app_final = gr.mount_gradio_app(app, demo, path="/gradio")
except Exception as e:
    print("[INFO] Ejecutando FastAPI directo:", e)
    app_final = app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    print(f"🚀 Iniciando servidor en puerto {port}...")
    uvicorn.run("app:app_final", host="0.0.0.0", port=port, reload=False)
