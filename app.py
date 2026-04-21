import os
import json
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request as GoogleAuthRequest

# ---------- CONFIGURACIÓN ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN no está configurado")

OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = os.environ.get("OAUTH_CLIENT_SECRET")
OAUTH_REFRESH_TOKEN = os.environ.get("OAUTH_REFRESH_TOKEN")
if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET or not OAUTH_REFRESH_TOKEN:
    raise ValueError("Faltan variables de entorno de OAuth (OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REFRESH_TOKEN)")

GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
if not GOOGLE_DRIVE_FOLDER_ID:
    raise ValueError("GOOGLE_DRIVE_FOLDER_ID no está configurado")

# Diccionario para almacenar sesiones de usuarios (en memoria)
user_sessions = {}

# ---------- FUNCIÓN DE AUTENTICACIÓN OAuth 2.0 ----------
def get_authenticated_service():
    """Construye y devuelve un servicio autenticado de Google Drive usando OAuth 2.0."""
    creds = Credentials(
        token=None,  # No necesitamos token inicial, usamos refresh_token
        refresh_token=OAUTH_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=OAUTH_CLIENT_ID,
        client_secret=OAUTH_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    # Si las credenciales están expiradas, las refrescamos automáticamente
    if creds.expired:
        creds.refresh(GoogleAuthRequest())
    drive_service = build('drive', 'v3', credentials=creds)
    return drive_service

# ---------- FUNCIÓN DE SUBIDA A GOOGLE DRIVE ----------
def upload_photo_to_drive(file_path, file_name, student_name, date_str, tipo, timestamp):
    """
    Sube un archivo a Google Drive organizándolo en:
    /student_name/date_str/tipo_timestamp/file_name
    """
    try:
        drive_service = get_authenticated_service()
        # Construir ruta de carpetas
        folder_path = f"{student_name}/{date_str}/{tipo}_{timestamp}"
        parent_id = GOOGLE_DRIVE_FOLDER_ID

        for folder in folder_path.split('/'):
            # Buscar si la carpeta ya existe
            query = f"name='{folder}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
            response = drive_service.files().list(
                q=query, spaces='drive', fields='files(id, name)', supportsAllDrives=False
            ).execute()
            folders = response.get('files', [])
            if folders:
                parent_id = folders[0]['id']
            else:
                # Crear la carpeta
                file_metadata = {
                    'name': folder,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [parent_id]
                }
                folder_obj = drive_service.files().create(body=file_metadata, fields='id', supportsAllDrives=False).execute()
                parent_id = folder_obj['id']

        # Subir el archivo
        media = MediaFileUpload(file_path, mimetype='image/jpeg')
        file_metadata = {'name': file_name, 'parents': [parent_id]}
        drive_service.files().create(body=file_metadata, media_body=media, fields='id', supportsAllDrives=False).execute()
        print(f"✅ Subido a Drive: {folder_path}/{file_name}")
        return True
    except Exception as e:
        print(f"❌ Error subiendo a Drive: {e}")
        return False

# ---------- MANEJADORES DEL BOT ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': None}
    print(f"🔵 /start - Usuario {user_id}")
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
        print(f"📝 Nombre: {nombre} (usuario {user_id})")
        keyboard = [
            [InlineKeyboardButton("📝 Tarea", callback_data='tarea')],
            [InlineKeyboardButton("🏋️ Actividad", callback_data='actividad')]
        ]
        await update.message.reply_text(
            f"✅ Gracias {nombre}. ¿Vas a enviar una tarea o una actividad?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def seleccionar_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    tipo = query.data
    if user_id in user_sessions:
        user_sessions[user_id]['tipo'] = tipo
        user_sessions[user_id]['fotos'] = []  # Reiniciar lista de fotos
        print(f"🔘 Tipo: {tipo} (usuario {user_id})")
        await query.edit_message_text(
            f"✅ Has seleccionado: *{tipo.upper()}*.\n\n"
            "Ahora envía *una o varias fotos* de tu trabajo.\n"
            "Puedes enviarlas todas juntas o una por una.\n"
            "Cuando termines, escribe /listo.",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("Por favor, inicia con /start.")

async def recibir_contenido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja fotos y documentos de imagen."""
    user_id = update.effective_user.id
    print(f"📥 recibir_contenido - Usuario {user_id}")

    if user_id not in user_sessions:
        await update.message.reply_text("Primero escribe /start para registrarte.")
        return

    session = user_sessions[user_id]
    if session['tipo'] is None:
        await update.message.reply_text("Primero selecciona tarea o actividad usando los botones.")
        return

    file = None
    extension = "jpg"
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        print("📸 Foto recibida")
    elif update.message.document:
        mime = update.message.document.mime_type
        if mime and mime.startswith('image/'):
            file = await update.message.document.get_file()
            original_name = update.message.document.file_name
            if original_name and '.' in original_name:
                extension = original_name.split('.')[-1]
            print(f"📄 Documento imagen recibido: {original_name}")
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
    await update.message.reply_text(f"📸 Recibido. Total en esta entrega: {len(session['fotos'])}.\nEnvía más o /listo.")

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

    await update.message.reply_text(f"⏳ Subiendo {len(session['fotos'])} archivo(s) a Google Drive...")

    success_count = 0
    total = len(session['fotos'])
    for idx, path in enumerate(session['fotos'], start=1):
        nombre_foto = f"pagina_{idx}.jpg"
        print(f"📤 Subiendo {path} -> {nombre_foto}")
        ok = upload_photo_to_drive(path, nombre_foto, estudiante, fecha_str, tipo, timestamp)
        if ok:
            success_count += 1
        # Eliminar archivo temporal
        try:
            os.remove(path)
        except:
            pass

    # Limpiar sesión
    session['fotos'] = []
    session['tipo'] = None

    if success_count == total:
        await update.message.reply_text(
            f"✅ *¡Entrega completa!*\n\n"
            f"Estudiante: {estudiante}\n"
            f"Tipo: {tipo}\n"
            f"Fecha: {fecha_str}\n"
            f"Archivos subidos: {success_count}\n\n"
            f"Puedes enviar otra tarea o actividad.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"⚠️ *Entrega parcial*\n\n"
            f"Se subieron {success_count} de {total} archivos.\n"
            f"Por favor intenta de nuevo.",
            parse_mode='Markdown'
        )

    # Volver a preguntar tipo
    keyboard = [
        [InlineKeyboardButton("📝 Tarea", callback_data='tarea')],
        [InlineKeyboardButton("🏋️ Actividad", callback_data='actividad')]
    ]
    await update.message.reply_text("¿Qué deseas enviar ahora?", reply_markup=InlineKeyboardMarkup(keyboard))

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        for path in user_sessions[user_id].get('fotos', []):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
        user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': user_sessions[user_id].get('estudiante')}
        await update.message.reply_text("Operación cancelada. Puedes empezar de nuevo con /start.")
    else:
        await update.message.reply_text("No hay sesión activa. Usa /start.")

# ---------- REGISTRO DE HANDLERS ----------
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("listo", finalizar_entrega))
bot_app.add_handler(CommandHandler("cancelar", cancelar))
bot_app.add_handler(CallbackQueryHandler(seleccionar_tipo))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre))
bot_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, recibir_contenido))

print("✅ Handlers registrados (fotos y documentos de imagen)")

# ---------- SERVIDOR FASTAPI ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot_app.initialize()
    await bot_app.start()
    print("🚀 Bot iniciado (webhook configurado manualmente)")
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

# ---------- PUNTO DE ENTRADA ----------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(api, host="0.0.0.0", port=port)
