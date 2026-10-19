FROM python:3.11-slim

# Instalar dependencias del sistema operativo requeridas por OpenCV y ONNX
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Configurar usuario estándar no-root (requerido por Hugging Face Spaces)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

# Instalar paquetes de Python
COPY --chown=user:user requirements.txt $HOME/app/
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Pre-descargar modelo biométrico ultra-ligero buffalo_s (optimizado para <512MB RAM)
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1
RUN python -c "from insightface.app import FaceAnalysis; app = FaceAnalysis(name='buffalo_s', allowed_modules=['detection', 'recognition']); app.prepare(ctx_id=-1, det_size=(320, 320))"

# Copiar archivos del proyecto
COPY --chown=user:user . $HOME/app

# Asegurar directorios de almacenamiento de fotos y base de datos
RUN mkdir -p $HOME/app/data/capturas $HOME/app/data/perfiles

# Puerto estándar para Render (10000) o variable de entorno PORT
EXPOSE 10000
ENV PORT=10000

CMD ["python", "run_server.py"]
