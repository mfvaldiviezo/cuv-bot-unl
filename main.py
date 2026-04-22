import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# Importar módulos locales
from config import TELEGRAM_TOKEN, TELEGRAM_SECRET_TOKEN, PORT
from database import init_db_pool, close_db_pool, init_tables
from handlers import (
    start, recibir_identificacion, seleccionar_tipo,
    recibir_contenido, finalizar_callback, cancelar_callback, fallback
)

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Crear aplicación Telegram
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

# Registrar handlers (orden IMPORTANTE)
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CallbackQueryHandler(seleccionar_tipo, pattern="^(tarea|actividad)$"))
bot_app.add_handler(CallbackQueryHandler(finalizar_callback, pattern="^finalizar$"))
bot_app.add_handler(CallbackQueryHandler(cancelar_callback, pattern="^cancelar$"))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_identificacion))
bot_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, recibir_contenido))
bot_app.add_handler(MessageHandler(filters.ALL, fallback))

# Lifecycle de FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialización y cleanup de la aplicación"""
    # Inicializar base de datos
    logger.info("🔄 Conectando a Supabase...")
    await init_db_pool()
    await init_tables()
    
    # Inicializar bot
    logger.info("🔄 Iniciando bot de Telegram...")
    await bot_app.initialize()
    await bot_app.start()
    
    logger.info("🚀 Bot + Supabase listos!")
    
    yield
    
    # Cleanup
    logger.info("🔄 Cerrando aplicación...")
    await bot_app.stop()
    await close_db_pool()
    logger.info("👋 Aplicación cerrada correctamente")

# Crear API FastAPI
api = FastAPI(
    lifespan=lifespan,
    title="Cálculo UV Bot API",
    description="Bot de Telegram para entregas de Cálculo",
    version="1.0.0"
)

# Endpoint webhook
@api.post("/webhook")
async def webhook(request: Request):
    """Procesar updates de Telegram"""
    try:
        # Verificar secret token (si está configurado)
        if TELEGRAM_SECRET_TOKEN:
            secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if secret_token != TELEGRAM_SECRET_TOKEN:
                logger.warning("⚠️ Webhook con token inválido")
                return {"status": "forbidden"}, 403
        
        # Procesar update
        req = await request.json()
        update = Update.de_json(req, bot_app.bot)
        await bot_app.process_update(update)
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"❌ Error en webhook: {e}", exc_info=True)
        return {"status": "error"}, 500

# Endpoint health check
@api.get("/health")
async def health_check():
    """Verificar estado de la aplicación"""
    return {
        "status": "healthy",
        "timestamp": __import__('datetime').datetime.now().isoformat()
    }

# Endpoint debug (solo desarrollo)
@api.get("/debug")
async def debug_info():
    """Información de debug"""
    from handlers import user_sessions
    return {
        "active_sessions": len(user_sessions),
        "bot_username": bot_app.bot.username if bot_app.bot else None
    }

# Punto de entrada principal
if __name__ == "__main__":
    import uvicorn
    logger.info(f"🌐 Iniciando servidor en puerto {PORT}")
    uvicorn.run(api, host="0.0.0.0", port=PORT)
