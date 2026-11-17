# Q.A.A. — Consultoría Integral Psicológica y Legal

Aplicación académica en desarrollo. La configuración inicial prepara Flask,
SQLAlchemy y Flask-Migrate; todavía no incluye la página institucional ni el
sistema de reservas.

## Requisitos

- Python 3.10 o posterior.
- Las dependencias listadas en `requirements-dev.txt` para desarrollar y ejecutar
  las pruebas.

## Preparación local

Desde la carpeta raíz del proyecto:

```bash
python -m venv .venv
```

Activa el entorno virtual:

```bash
# Linux o macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Instala las dependencias y prepara la configuración local:

```bash
python -m pip install -r requirements-dev.txt
```

Copia `.env.example` como `.env`. Para desarrollo, puedes mantener SQLite local;
antes de publicar, genera una clave secreta propia y configura una URL de
PostgreSQL con el controlador Psycopg 3, por ejemplo
`postgresql+psycopg://usuario:clave@servidor/base_datos`.

## Ejecutar la aplicación

```bash
python run.py
```

Abre <http://127.0.0.1:5000/>. Por ahora verás un mensaje temporal; la página
pública se construirá en una etapa posterior. El servidor incluido en Flask es
solo para desarrollo local.

## Pruebas

```bash
python -m pytest
```

La prueba inicial comprueba que la aplicación responde en la ruta principal.
Las pruebas de reservas, expiración, conflictos y estados se agregarán junto con
esas funcionalidades.

## Migraciones de base de datos

Flask-Migrate ofrece comandos para crear y aplicar migraciones cuando existan
modelos. Una migración registra los cambios en las tablas para aplicarlos de
forma controlada a SQLite o PostgreSQL.

```bash
flask --app run.py db init
flask --app run.py db migrate -m "descripcion del cambio"
flask --app run.py db upgrade
```

El primer `db init` se ejecuta una sola vez. Las migraciones generadas deben
revisarse antes de aplicarlas y guardarse en el proyecto.
