import os
import json
import datetime
import sqlite3
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
    raise ValueError("Faltan variables de entorno de OAuth")

GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
if not GOOGLE_DRIVE_FOLDER_ID:
    raise ValueError("GOOGLE_DRIVE_FOLDER_ID no está configurado")

# ---------- BASE DE DATOS ----------
DB_PATH = "/tmp/estudiantes.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS estudiantes
                 (user_id INTEGER PRIMARY KEY,
                  nombre TEXT,
                  codigo TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()
    print("✅ Base de datos inicializada")

def get_estudiante(user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT nombre, codigo FROM estudiantes WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        return row
    except Exception as e:
        print(f"❌ Error get_estudiante: {e}")
        return None

def save_estudiante(user_id, nombre=None, codigo=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        if nombre:
            c.execute("INSERT OR REPLACE INTO estudiantes (user_id, nombre) VALUES (?, ?)", (user_id, nombre))
        elif codigo:
            c.execute("INSERT OR REPLACE INTO estudiantes (user_id, codigo) VALUES (?, ?)", (user_id, codigo))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Error save_estudiante: {e}")
        return False

init_db()

# ---------- SESIONES EN MEMORIA ----------
user_sessions = {}

# ---------- GOOGLE DRIVE ----------
def get_authenticated_service():
    creds = Credentials(
        token=None,
        refresh_token=OAUTH_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=OAUTH_CLIENT_ID,
        client_secret=OAUTH_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    if creds.expired:
        creds.refresh(GoogleAuthRequest())
    return build('drive', 'v3', credentials=creds)

def upload_photo_to_drive(file_path, file_name, student_name, date_str, tipo, timestamp):
    try:
        drive_service = get_authenticated_service()
        folder_path = f"{student_name}/{date_str}/{tipo}_{timestamp}"
        parent_id = GOOGLE_DRIVE_FOLDER_ID

        for folder in folder_path.split('/'):
            query = f"name='{folder}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
            response = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)', supportsAllDrives=False).execute()
            folders = response.get('files', [])
            if folders:
                parent_id = folders[0]['id']
            else:
                file_metadata = {
                    'name': folder,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [parent_id]
                }
                folder_obj = drive_service.files().create(body=file_metadata, fields='id', supportsAllDrives=False).execute()
                parent_id = folder_obj['id']

        media = MediaFileUpload(file_path, mimetype='image/jpeg')
        file_metadata = {'name': file_name, 'parents': [parent_id]}
        drive_service.files().create(body=file_metadata, media_body=media, fields='id', supportsAllDrives=False).execute()
        print(f"✅ Subido a Drive: {folder_path}/{file_name}")
        return True
    except Exception as e:
        print(f"❌ Error subiendo a Drive: {e}")
        return False

# ---------- TECLADOS ----------
def get_control_keyboard(has_photos=False):
    keyboard = []
    if has_photos:
        keyboard.append([InlineKeyboardButton("✅ Finalizar entrega", callback_data="finalizar")])
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")])
    return InlineKeyboardMarkup(keyboard)

# ---------- HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    print(f"🔵 /start recibido de usuario {user_id}")
    
    # Verificar si ya está registrado
    estudiante = get_estudiante(user_id)
    print(f"   Estudiante en BD: {estudiante}")
    
    if estudiante and (estudiante[0] or estudiante[1]):
        nombre_mostrar = estudiante[0] if estudiante[0] else estudiante[1]
        user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': nombre_mostrar}
        await update.message.reply_text(
            f"👋 ¡Bienvenido de vuelta {nombre_mostrar}!"
        )
        keyboard = [
            [InlineKeyboardButton("📝 Tarea", callback_data="tarea")],
            [InlineKeyboardButton("🏋️ Actividad", callback_data="actividad")]
        ]
        await update.message.reply_text(
            "¿Qué vas a enviar?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Nuevo usuario: crear sesión y pedir nombre/código
    user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': None}
    await update.message.reply_text(
        "📚 *Bienvenido al Bot de Cálculo de una variable*\n\n"
        "Para identificarte, escribe tu **nombre completo** o un **código** (ej: EST12345).\n"
        "Así aparecerá en Google Drive.",
        parse_mode='Markdown'
    )
    context.user_data['esperando_identificacion'] = True

async def recibir_identificacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get('esperando_identificacion'):
        texto = update.message.text.strip()
        # Guardar en BD
        ok = save_estudiante(user_id, nombre=texto, codigo=None)
        if not ok:
            await update.message.reply_text("❌ Error al guardar tus datos. Intenta de nuevo más tarde.")
            return
        # Crear sesión
        if user_id in user_sessions:
            user_sessions[user_id]['estudiante'] = texto
        else:
            user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': texto}
        context.user_data['esperando_identificacion'] = False
        print(f"✅ Nuevo estudiante registrado: {texto} (ID: {user_id})")
        keyboard = [
            [InlineKeyboardButton("📝 Tarea", callback_data="tarea")],
            [InlineKeyboardButton("🏋️ Actividad", callback_data="actividad")]
        ]
        await update.message.reply_text(
            f"✅ Gracias {texto}. ¿Qué vas a enviar?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def seleccionar_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    tipo = query.data
    if user_id not in user_sessions:
        estudiante = get_estudiante(user_id)
        if estudiante and (estudiante[0] or estudiante[1]):
            nombre = estudiante[0] if estudiante[0] else estudiante[1]
            user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': nombre}
        else:
            await query.edit_message_text("Por favor, inicia con /start")
            return
    session = user_sessions[user_id]
    session['tipo'] = tipo
    session['fotos'] = []
    await query.edit_message_text(
        f"✅ Tipo *{tipo.upper()}* seleccionado.\n\n"
        "Ahora envía *una o varias fotos* de tu trabajo.\n"
        "Cuando termines, presiona **Finalizar entrega**.",
        parse_mode='Markdown',
        reply_markup=get_control_keyboard(has_photos=False)
    )

async def recibir_contenido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        estudiante = get_estudiante(user_id)
        if estudiante and (estudiante[0] or estudiante[1]):
            nombre = estudiante[0] if estudiante[0] else estudiante[1]
            user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': nombre}
        else:
            await update.message.reply_text("Primero usa /start para registrarte.")
            return

    session = user_sessions[user_id]
    if session['tipo'] is None:
        await update.message.reply_text("Primero selecciona tarea o actividad usando los botones.")
        return

    # Procesar foto
    file = None
    extension = "jpg"
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
    elif update.message.document and update.message.document.mime_type.startswith('image/'):
        file = await update.message.document.get_file()
        if update.message.document.file_name and '.' in update.message.document.file_name:
            extension = update.message.document.file_name.split('.')[-1]
    else:
        await update.message.reply_text("Envía una imagen (foto o documento).")
        return

    timestamp = datetime.datetime.now().strftime("%H%M%S%f")[:-3]
    local_path = f"/tmp/{user_id}_{timestamp}.{extension}"
    await file.download_to_drive(local_path)
    session['fotos'].append(local_path)
    total = len(session['fotos'])
    await update.message.reply_text(
        f"📸 Recibido. Total: {total}.\n\nPresiona 'Finalizar entrega' cuando termines.",
        reply_markup=get_control_keyboard(has_photos=True)
    )

async def finalizar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in user_sessions:
        await query.edit_message_text("Usa /start primero.")
        return
    session = user_sessions[user_id]
    if not session['fotos']:
        await query.edit_message_text("No hay fotos para finalizar.")
        return

    estudiante = session['estudiante']
    tipo = session['tipo']
    now = datetime.datetime.now()
    fecha_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%H%M%S")

    await query.edit_message_text(f"⏳ Subiendo {len(session['fotos'])} archivo(s)...")

    success = 0
    total = len(session['fotos'])
    for idx, path in enumerate(session['fotos'], 1):
        nombre_foto = f"pagina_{idx}.jpg"
        if upload_photo_to_drive(path, nombre_foto, estudiante, fecha_str, tipo, timestamp):
            success += 1
        try:
            os.remove(path)
        except:
            pass

    session['fotos'] = []
    session['tipo'] = None

    await query.message.reply_text(
        f"✅ *Entrega completa*\n\nEstudiante: {estudiante}\nTipo: {tipo}\nFecha: {fecha_str}\nSubidas: {success}/{total}",
        parse_mode='Markdown'
    )
    keyboard = [
        [InlineKeyboardButton("📝 Tarea", callback_data="tarea")],
        [InlineKeyboardButton("🏋️ Actividad", callback_data="actividad")]
    ]
    await query.message.reply_text("¿Otra entrega?", reply_markup=InlineKeyboardMarkup(keyboard))

async def cancelar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id in user_sessions:
        for path in user_sessions[user_id].get('fotos', []):
            try:
                os.remove(path)
            except:
                pass
        user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': user_sessions[user_id].get('estudiante')}
    await query.edit_message_text("Operación cancelada. Usa /start para empezar de nuevo.")

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captura cualquier mensaje no manejado y pide /start"""
    await update.message.reply_text("⚠️ Por favor, usa /start para comenzar.")

# ---------- REGISTRO DE HANDLERS (orden importante) ----------
bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))  # Este debe ser el primero
bot_app.add_handler(CallbackQueryHandler(seleccionar_tipo, pattern="^(tarea|actividad)$"))
bot_app.add_handler(CallbackQueryHandler(finalizar_callback, pattern="^finalizar$"))
bot_app.add_handler(CallbackQueryHandler(cancelar_callback, pattern="^cancelar$"))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_identificacion))
bot_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, recibir_contenido))
bot_app.add_handler(MessageHandler(filters.ALL, fallback))  # Último

# ---------- SERVIDOR FASTAPI ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot_app.initialize()
    await bot_app.start()
    print("🚀 Bot iniciado correctamente")
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
        print(f"❌ Error en webhook: {e}")
        return {"status": "error"}, 500

@api.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(api, host="0.0.0.0", port=port)
