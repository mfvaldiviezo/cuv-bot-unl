import os
import json
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# --- Configuración desde Variables de Entorno ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN no está configurado")

# Diccionario para almacenar las sesiones activas de los usuarios
user_sessions = {}

# --- Manejadores del Bot ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': None}
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
        user_sessions[user_id]['fotos'] = []
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
    if user_id not in user_sessions:
        await update.message.reply_text("Primero escribe /start para registrarte.")
        return

    session = user_sessions[user_id]
    if session['tipo'] is None:
        await update.message.reply_text("Primero elige si es tarea o actividad usando los botones.")
        return

    photos = update.message.photo
    if not photos:
        return

    downloaded = []
    for i, photo in enumerate(photos):
        file = await photo[-1].get_file()
        timestamp = datetime.datetime.now().strftime("%H%M%S%f")[:-3]
        local_path = f"/tmp/{user_id}_{timestamp}_{i}.jpg"
        await file.download_to_drive(local_path)
        downloaded.append(local_path)

    session['fotos'].extend(downloaded)
    await update.message.reply_text(f"📸 Recibidas {len(photos)} foto(s). Total en esta entrega: {len(session['fotos'])}.\nEnvía más o escribe /listo para finalizar.")

async def finalizar_entrega(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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

    await update.message.reply_text("⏳ Subiendo tus archivos a Google Drive...")

    # Aquí iría la lógica de subida a Google Drive
    # Por ahora, solo simulamos la subida
    for idx, path in enumerate(session['fotos'], start=1):
        nombre_foto = f"pagina_{idx}.jpg"
        # upload_photo_to_drive(path, nombre_foto, estudiante, fecha_str, tipo, timestamp)
        os.remove(path)

    session['fotos'] = []
    session['tipo'] = None

    await update.message.reply_text(
        f"✅ *¡Entrega completa!*\n\n"
        f"Estudiante: {estudiante}\n"
        f"Tipo: {tipo}\n"
        f"Fecha: {fecha_str}\n"
        f"Fotos subidas: {idx}\n\n"
        f"Ya puedes enviar otra tarea o actividad. Para cambiar de estudiante, usa /start de nuevo.",
        parse_mode='Markdown'
    )

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

# --- Crear la aplicación del bot y registrar los handlers ---
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("listo", finalizar_entrega))
bot_app.add_handler(CommandHandler("cancelar", cancelar))
bot_app.add_handler(CallbackQueryHandler(seleccionar_tipo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre))
bot_app.add_handler(MessageHandler(filters.PHOTO, recibir_fotos))

# --- Aplicación FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicializar la aplicación del bot sin configurar el webhook
    await bot_app.initialize()
    await bot_app.start()
    print("🚀 Bot iniciado correctamente. Webhook no configurado automáticamente.")
    yield
    await bot_app.stop()
    print("🛑 Bot detenido")

api = FastAPI(lifespan=lifespan)

@api.post("/webhook")
async def process_telegram_update(request: Request):
    req = await request.json()
    update = Update.de_json(req, bot_app.bot)
    await bot_app.process_update(update)
    return {"status": "ok"}

@api.get("/health")
async def health():
    return {"status": "healthy"}

# --- Punto de entrada ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(api, host="0.0.0.0", port=port)