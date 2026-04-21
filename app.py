import os
import json
import datetime
import asyncio
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ---------- CONFIGURACIÓN ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN no está configurado")

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
if not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("GOOGLE_CREDENTIALS_JSON no está configurado")

GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
if not GOOGLE_DRIVE_FOLDER_ID:
    raise ValueError("GOOGLE_DRIVE_FOLDER_ID no está configurado")

# Diccionario para almacenar sesiones activas
user_sessions = {}

# ---------- FUNCIONES DE GOOGLE DRIVE ----------
def upload_photo_to_drive(file_path, file_name, student_name, date_str, tipo, timestamp):
    # Cargar credenciales desde la variable de entorno
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    drive_service = build('drive', 'v3', credentials=creds)

    # Ruta: Estudiante/Fecha/Tipo_HHMMSS/
    folder_path = f"{student_name}/{date_str}/{tipo}_{timestamp}"

    parent_id = GOOGLE_DRIVE_FOLDER_ID
    for folder in folder_path.split('/'):
        query = f"name='{folder}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
        response = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        folders = response.get('files', [])
        if folders:
            parent_id = folders[0]['id']
        else:
            file_metadata = {
                'name': folder,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id]
            }
            folder_obj = drive_service.files().create(body=file_metadata, fields='id').execute()
            parent_id = folder_obj['id']

    media = MediaFileUpload(file_path, mimetype='image/jpeg')
    file_metadata = {'name': file_name, 'parents': [parent_id]}
    drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return parent_id

# ---------- MANEJADORES DEL BOT ----------
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

    for idx, path in enumerate(session['fotos'], start=1):
        nombre_foto = f"pagina_{idx}.jpg"
        upload_photo_to_drive(path, nombre_foto, estudiante, fecha_str, tipo, timestamp)
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

# ---------- APLICACIÓN FLASK ----------
app = Flask(__name__)

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=['POST'])
async def webhook():
    """Endpoint que recibe las actualizaciones de Telegram"""
    try:
        # Obtener la actualización en formato JSON
        update = Update.de_json(request.get_json(force=True), bot_app.bot)
        # Procesar la actualización de forma asíncrona
        await bot_app.process_update(update)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        # En caso de error, lo registramos y devolvemos un error 500
        print(f"Error en webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/health", methods=['GET'])
def health():
    """Endpoint de salud para que Render verifique que el servicio está vivo"""
    return jsonify({"status": "healthy"}), 200

# ---------- INICIALIZACIÓN DEL BOT ----------
# Crear la aplicación del bot y registrar los handlers
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("listo", finalizar_entrega))
bot_app.add_handler(CommandHandler("cancelar", cancelar))
bot_app.add_handler(CallbackQueryHandler(seleccionar_tipo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre))
bot_app.add_handler(MessageHandler(filters.PHOTO, recibir_fotos))

# Configurar el webhook al iniciar la aplicación
async def setup_webhook():
    """Configura el webhook en la API de Telegram"""
    # Construir la URL del webhook
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_URL')}/webhook/{TELEGRAM_TOKEN}"
    
    # Eliminar cualquier webhook existente
    await bot_app.bot.delete_webhook()
    
    # Configurar el nuevo webhook
    await bot_app.bot.set_webhook(url=webhook_url)
    print(f"✅ Webhook configurado: {webhook_url}")

# Esta función se ejecuta antes de que el servidor Flask comience a servir peticiones
@app.before_first_request
def initialize():
    """Función que se ejecuta una sola vez al iniciar el servidor"""
    # Crear un nuevo event loop para ejecutar la configuración del webhook
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(setup_webhook())
    loop.close()

if __name__ == "__main__":
    # Si se ejecuta directamente, iniciar el servidor Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)