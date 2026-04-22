# database.py
import os
import logging
from typing import Optional, Tuple
from psycopg_pool import AsyncConnectionPool
import psycopg

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL no está configurado")

# Asegurar sslmode en la URL
if "sslmode" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"

db_pool: Optional[AsyncConnectionPool] = None

async def init_db_pool():
    """Inicializar pool de conexiones con psycopg"""
    global db_pool
    try:
        db_pool = AsyncConnectionPool(
            conninfo=DATABASE_URL,
            min_size=2,
            max_size=10,
            open=False,
            kwargs={"sslmode": "require", "connect_timeout": 30}
        )
        await db_pool.open()
        logger.info("✅ Pool de Supabase (psycopg) inicializado correctamente")
    except Exception as e:
        logger.error(f"❌ Error inicializando Supabase: {e}", exc_info=True)
        raise

async def close_db_pool():
    """Cerrar pool de conexiones"""
    global db_pool
    if db_pool:
        await db_pool.close()
        logger.info("🔴 Pool de Supabase cerrado")

async def init_tables():
    """Crear tablas si no existen"""
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            # Tabla estudiantes
            await cur.execute('''
                CREATE TABLE IF NOT EXISTS estudiantes (
                    user_id BIGINT PRIMARY KEY,
                    nombre TEXT,
                    codigo TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            ''')
            # Índice
            await cur.execute('''
                CREATE INDEX IF NOT EXISTS idx_estudiantes_user_id 
                ON estudiantes(user_id)
            ''')
            await conn.commit()
        logger.info("✅ Tablas inicializadas en Supabase")

async def get_estudiante(user_id: int) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Obtener estudiante por user_id"""
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT nombre, codigo FROM estudiantes WHERE user_id = %s",
                    (user_id,)
                )
                row = await cur.fetchone()
                return (row[0], row[1]) if row else None
    except Exception as e:
        logger.error(f"❌ Error get_estudiante: {e}", exc_info=True)
        return None

async def save_estudiante(user_id: int, nombre: str = None, codigo: str = None) -> bool:
    """Guardar o actualizar estudiante"""
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                if nombre:
                    await cur.execute('''
                        INSERT INTO estudiantes (user_id, nombre) 
                        VALUES (%s, %s) 
                        ON CONFLICT (user_id) 
                        DO UPDATE SET nombre = %s, updated_at = NOW()
                    ''', (user_id, nombre, nombre))
                elif codigo:
                    await cur.execute('''
                        INSERT INTO estudiantes (user_id, codigo) 
                        VALUES (%s, %s) 
                        ON CONFLICT (user_id) 
                        DO UPDATE SET codigo = %s, updated_at = NOW()
                    ''', (user_id, codigo, codigo))
                await conn.commit()
                return True
    except Exception as e:
        logger.error(f"❌ Error save_estudiante: {e}", exc_info=True)
        return False
