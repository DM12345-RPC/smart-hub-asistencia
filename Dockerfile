FROM python:3.11-slim

# Instalar dependencias del sistema operativo requeridas por OpenCV y ONNX
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Configurar usuario estándar no-root
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    ORT_DISABLE_MEMORY_ARENA=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR $HOME/app

# Instalar paquetes de Python
COPY --chown=user:user requirements.txt $HOME/app/
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Pre-descargar modelo buffalo_s y purgar modelos 3D y de edad pesados para garantizar <120MB de RAM
RUN python -c "from insightface.app import FaceAnalysis; app = FaceAnalysis(name='buffalo_s'); app.prepare(ctx_id=-1, det_size=(320, 320))" && \
    rm -f /home/user/.insightface/models/buffalo_s/1k3d68.onnx \
          /home/user/.insightface/models/buffalo_s/2d106det.onnx \
          /home/user/.insightface/models/buffalo_s/genderage.onnx

# Copiar archivos del proyecto
COPY --chown=user:user . $HOME/app

# Asegurar directorios de almacenamiento de fotos y base de datos
RUN mkdir -p $HOME/app/data/capturas $HOME/app/data/perfiles

# Puerto estándar para Render (10000) o variable de entorno PORT
EXPOSE 10000
ENV PORT=10000

CMD ["python", "run_server.py"]
