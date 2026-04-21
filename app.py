import os
import json
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN no está configurado")

user_sessions = {}

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': None}
    print(f"🔵 /start - Usuario {user_id}")
    await update.message.reply_text(
        "📚 *Bienvenido al Bot de Cálculo de una variable*\n\n"
        "Primero, dime tu nombre completo:",
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
        print(f"📝 Nombre: {nombre} (usuario {user_id})")
        keyboard = [
            [InlineKeyboardButton("📝 Tarea", callback_data='tarea')],
            [InlineKeyboardButton("🏋️ Actividad", callback_data='actividad')]
        ]
        await update.message.reply_text(
            f"✅ Gracias {nombre}. ¿Tarea o actividad?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def seleccionar_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    tipo = query.data
    if user_id in user_sessions:
        user_sessions[user_id]['tipo'] = tipo
        user_sessions[user_id]['fotos'] = []
        print(f"🔘 Tipo: {tipo} (usuario {user_id})")
        await query.edit_message_text(
            f"✅ Tipo *{tipo.upper()}* seleccionado.\n\n"
            "Envía una o varias fotos de tu trabajo.\n"
            "Cuando termines, escribe /listo.",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("Usa /start primero.")

async def recibir_contenido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja tanto fotos como documentos de imagen."""
    user_id = update.effective_user.id
    print(f"📥 recibir_contenido - Usuario {user_id}")

    if user_id not in user_sessions:
        await update.message.reply_text("Primero /start.")
        return

    session = user_sessions[user_id]
    if session['tipo'] is None:
        await update.message.reply_text("Primero selecciona tarea o actividad con los botones.")
        return

    # Verificar si es foto o documento
    file = None
    if update.message.photo:
        # Es una foto
        file = await update.message.photo[-1].get_file()
        extension = "jpg"
        print(f"📸 Foto recibida")
    elif update.message.document:
        # Es un documento, verificar si es imagen
        mime = update.message.document.mime_type
        if mime and mime.startswith('image/'):
            file = await update.message.document.get_file()
            extension = update.message.document.file_name.split('.')[-1] if update.message.document.file_name else "jpg"
            print(f"📄 Documento imagen recibido: {update.message.document.file_name}")
        else:
            await update.message.reply_text("Por favor envía una imagen (foto o documento de imagen).")
            return
    else:
        await update.message.reply_text("No detecté ninguna imagen. Envía una foto o un documento de imagen.")
        return

    # Descargar archivo
    timestamp = datetime.datetime.now().strftime("%H%M%S%f")[:-3]
    local_path = f"/tmp/{user_id}_{timestamp}.{extension}"
    await file.download_to_drive(local_path)
    session['fotos'].append(local_path)
    print(f"✅ Archivo guardado: {local_path} (total: {len(session['fotos'])})")
    await update.message.reply_text(f"📸 Recibido. Total: {len(session['fotos'])}. Envía más o /listo.")

async def finalizar_entrega(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    print(f"🏁 finalizar_entrega - Usuario {user_id}")

    if user_id not in user_sessions:
        await update.message.reply_text("Usa /start.")
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

    await update.message.reply_text(f"⏳ Subiendo {len(session['fotos'])} archivo(s)...")

    for idx, path in enumerate(session['fotos'], start=1):
        nombre_foto = f"pagina_{idx}.jpg"
        print(f"📤 Subiendo {path} -> {nombre_foto}")
        # Aquí iría upload_photo_to_drive...
        os.remove(path)

    session['fotos'] = []
    session['tipo'] = None

    await update.message.reply_text(
        f"✅ *Entrega completa*\n\n"
        f"Estudiante: {estudiante}\n"
        f"Tipo: {tipo}\n"
        f"Fecha: {fecha_str}\n"
        f"Archivos: {idx}",
        parse_mode='Markdown'
    )

    keyboard = [[InlineKeyboardButton("📝 Tarea", callback_data='tarea')], [InlineKeyboardButton("🏋️ Actividad", callback_data='actividad')]]
    await update.message.reply_text("¿Otra entrega?", reply_markup=InlineKeyboardMarkup(keyboard))

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        for path in user_sessions[user_id].get('fotos', []):
            if os.path.exists(path): os.remove(path)
        user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': user_sessions[user_id].get('estudiante')}
        await update.message.reply_text("Operación cancelada.")
    else:
        await update.message.reply_text("No hay sesión activa.")

# --- Registrar handlers ---
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("listo", finalizar_entrega))
bot_app.add_handler(CommandHandler("cancelar", cancelar))
bot_app.add_handler(CallbackQueryHandler(seleccionar_tipo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre))
# Este es el handler clave: captura fotos y documentos
bot_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, recibir_contenido))

print("✅ Handlers registrados (incluye photos y documents)")

# --- FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot_app.initialize()
    await bot_app.start()
    print("🚀 Bot iniciado")
    yield
    await bot_app.stop()

api = FastAPI(lifespan=lifespan)

@api.post("/webhook")
async def webhook(request: Request):
    try:
        req = await request.json()
        update = Update.de_json(req, bot_app.bot)
        await bot_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        print(f"❌ Error: {e}")
        return {"status": "error"}, 500

@api.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(api, host="0.0.0.0", port=port)
