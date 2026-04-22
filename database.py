import asyncpg
import os
import logging
from typing import Optional, Tuple, List
from datetime import datetime

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL no está configurado en las variables de entorno")

db_pool: Optional[asyncpg.Pool] = None

async def init_db_pool():
    """Inicializar pool de conexiones a Supabase"""
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=60,
            ssl='require'  # Supabase requiere SSL
        )
        logger.info("✅ Pool de Supabase inicializado correctamente")
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
    async with db_pool.acquire() as conn:
        # Tabla estudiantes
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS estudiantes (
                user_id BIGINT PRIMARY KEY,
                nombre TEXT,
                codigo TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')
        
        # Índices
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_estudiantes_user_id 
            ON estudiantes(user_id)
        ''')
        
        logger.info("✅ Tablas inicializadas en Supabase")

async def get_estudiante(user_id: int) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Obtener estudiante por user_id"""
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT nombre, codigo FROM estudiantes WHERE user_id = $1",
                user_id
            )
            return (row['nombre'], row['codigo']) if row else None
    except Exception as e:
        logger.error(f"❌ Error get_estudiante: {e}", exc_info=True)
        return None

async def save_estudiante(user_id: int, nombre: str = None, codigo: str = None) -> bool:
    """Guardar o actualizar estudiante"""
    try:
        async with db_pool.acquire() as conn:
            if nombre:
                await conn.execute('''
                    INSERT INTO estudiantes (user_id, nombre) 
                    VALUES ($1, $2) 
                    ON CONFLICT (user_id) 
                    DO UPDATE SET nombre = $2, updated_at = NOW()
                ''', user_id, nombre)
            elif codigo:
                await conn.execute('''
                    INSERT INTO estudiantes (user_id, codigo) 
                    VALUES ($1, $2) 
                    ON CONFLICT (user_id) 
                    DO UPDATE SET codigo = $2, updated_at = NOW()
                ''', user_id, codigo)
            return True
    except Exception as e:
        logger.error(f"❌ Error save_estudiante: {e}", exc_info=True)
        return False

async def count_estudiantes() -> int:
    """Contar total de estudiantes registrados"""
    try:
        async with db_pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM estudiantes")
            return count or 0
    except Exception as e:
        logger.error(f"❌ Error count_estudiantes: {e}", exc_info=True)
        return 0
