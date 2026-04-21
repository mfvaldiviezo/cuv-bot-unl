import os
import json
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# --- Configuración ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN no está configurado")

# Diccionario de sesiones (persiste mientras el bot esté corriendo)
user_sessions = {}

# --- Funciones de los handlers (con logs) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': None}
    print(f"🔵 /start - Usuario {user_id} - Sesión creada")
    await update.message.reply_text(
        "📚 *Bienvenido al Bot de Cálculo de una variable*\n\n"
        "Primero, dime tu nombre completo (como quieras que aparezca en Drive):",
        parse_mode='Markdown'
    )
    context.user_data['esperando_nombre'] = True

async def recibir_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get('esperando_nombre'):
        nombre = update.message.text.strip()
        if user_id in user_sessions:
            user_sessions[user_id]['estudiante'] = nombre
        else:
            user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': nombre}
        context.user_data['esperando_nombre'] = False
        print(f"📝 Nombre recibido - Usuario {user_id}: {nombre}")
        keyboard = [
            [InlineKeyboardButton("📝 Tarea", callback_data='tarea')],
            [InlineKeyboardButton("🏋️ Actividad", callback_data='actividad')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"✅ Gracias {nombre}. Ahora, ¿vas a enviar una tarea o una actividad?",
            reply_markup=reply_markup
        )

async def seleccionar_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    tipo = query.data
    if user_id in user_sessions:
        user_sessions[user_id]['tipo'] = tipo
        user_sessions[user_id]['fotos'] = []  # Reiniciar fotos
        print(f"🔘 Tipo seleccionado - Usuario {user_id}: {tipo}")
        await query.edit_message_text(
            f"✅ Has seleccionado: *{tipo.upper()}*.\n\n"
            "Ahora envía *una o varias fotos* de tu trabajo.\n"
            "Puedes enviarlas todas juntas en un solo mensaje (seleccionando varias) o una por una.\n"
            "Cuando termines, escribe /listo.",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("Por favor, inicia con /start")

async def recibir_fotos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    print(f"📸 recibir_fotos llamado - Usuario {user_id}")

    if user_id not in user_sessions:
        await update.message.reply_text("Primero escribe /start para registrarte.")
        return

    session = user_sessions[user_id]
    if session['tipo'] is None:
        await update.message.reply_text("Primero elige si es tarea o actividad usando los botones.")
        return

    # Verificar si hay fotos en el mensaje
    if not update.message.photo:
        print("⚠️ No se encontraron fotos en update.message.photo")
        await update.message.reply_text("No se detectaron fotos. Por favor envía una imagen.")
        return

    photos = update.message.photo
    print(f"📸 Cantidad de fotos recibidas: {len(photos)}")

    downloaded = []
    for i, photo in enumerate(photos):
        # La última es la de mayor resolución
        file = await photo[-1].get_file()
        timestamp = datetime.datetime.now().strftime("%H%M%S%f")[:-3]
        local_path = f"/tmp/{user_id}_{timestamp}_{i}.jpg"
        await file.download_to_drive(local_path)
        downloaded.append(local_path)
        print(f"   - Foto {i+1} descargada: {local_path}")

    session['fotos'].extend(downloaded)
    await update.message.reply_text(
        f"📸 Recibidas {len(photos)} foto(s). Total en esta entrega: {len(session['fotos'])}.\n"
        "Envía más o escribe /listo para finalizar."
    )
    print(f"✅ Total fotos acumuladas: {len(session['fotos'])}")

async def finalizar_entrega(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    print(f"🏁 finalizar_entrega - Usuario {user_id}")

    if user_id not in user_sessions:
        await update.message.reply_text("Primero escribe /start.")
        return

    session = user_sessions[user_id]
    if not session['fotos']:
        await update.message.reply_text("No has enviado ninguna foto. Envía una o más fotos y luego /listo.")
        return

    estudiante = session['estudiante']
    tipo = session['tipo']
    now = datetime.datetime.now()
    fecha_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%H%M%S")

    await update.message.reply_text(f"⏳ Subiendo {len(session['fotos'])} archivos a Google Drive...")

    # Aquí iría la subida a Google Drive (por ahora simulamos)
    for idx, path in enumerate(session['fotos'], start=1):
        nombre_foto = f"pagina_{idx}.jpg"
        print(f"📤 Subiendo {path} -> {nombre_foto}")
        # upload_photo_to_drive(path, nombre_foto, estudiante, fecha_str, tipo, timestamp)
        os.remove(path)  # Eliminar temporal

    # Limpiar sesión
    session['fotos'] = []
    session['tipo'] = None

    await update.message.reply_text(
        f"✅ *¡Entrega completa!*\n\n"
        f"Estudiante: {estudiante}\n"
        f"Tipo: {tipo}\n"
        f"Fecha: {fecha_str}\n"
        f"Fotos subidas: {idx}\n\n"
        f"Ya puedes enviar otra tarea o actividad.",
        parse_mode='Markdown'
    )

    # Volver a preguntar tipo
    keyboard = [
        [InlineKeyboardButton("📝 Tarea", callback_data='tarea')],
        [InlineKeyboardButton("🏋️ Actividad", callback_data='actividad')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("¿Qué deseas enviar ahora?", reply_markup=reply_markup)

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        for path in user_sessions[user_id].get('fotos', []):
            if os.path.exists(path):
                os.remove(path)
        user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': user_sessions[user_id].get('estudiante')}
        await update.message.reply_text("Operación cancelada. Puedes empezar de nuevo con /start")
    else:
        await update.message.reply_text("No hay operación activa. Usa /start")

# --- Crear la aplicación del bot ---
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("listo", finalizar_entrega))
bot_app.add_handler(CommandHandler("cancelar", cancelar))
bot_app.add_handler(CallbackQueryHandler(seleccionar_tipo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre))
bot_app.add_handler(MessageHandler(filters.PHOTO, recibir_fotos))

print("✅ Handlers registrados correctamente")

# --- FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot_app.initialize()
    await bot_app.start()
    print("🚀 Bot iniciado (sin webhook automático)")
    yield
    await bot_app.stop()
    print("🛑 Bot detenido")

api = FastAPI(lifespan=lifespan)

@api.post("/webhook")
async def webhook(request: Request):
    try:
        req = await request.json()
        update = Update.de_json(req, bot_app.bot)
        await bot_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        print(f"❌ Error en webhook: {e}")
        return {"status": "error", "message": str(e)}, 500

@api.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(api, host="0.0.0.0", port=port)
