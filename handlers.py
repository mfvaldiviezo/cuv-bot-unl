import io
import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import get_estudiante, save_estudiante
from drive_service import upload_photo_to_drive

logger = logging.getLogger(__name__)

# Sesiones en memoria (se reconstruyen desde DB si es necesario)
user_sessions = {}

def get_control_keyboard(has_photos: bool = False) -> InlineKeyboardMarkup:
    """Generar teclado de control"""
    keyboard = []
    if has_photos:
        keyboard.append([InlineKeyboardButton("✅ Finalizar entrega", callback_data="finalizar")])
    keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")])
    return InlineKeyboardMarkup(keyboard)

def get_main_keyboard() -> InlineKeyboardMarkup:
    """Teclado principal"""
    keyboard = [
        [InlineKeyboardButton("📝 Tarea", callback_data="tarea")],
        [InlineKeyboardButton("🏋️ Actividad", callback_data="actividad")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler /start"""
    user_id = update.effective_user.id
    logger.info(f"🔵 /start recibido de usuario {user_id}")
    
    estudiante = await get_estudiante(user_id)
    
    if estudiante and (estudiante[0] or estudiante[1]):
        # Usuario registrado
        nombre_mostrar = estudiante[0] if estudiante[0] else estudiante[1]
        user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': nombre_mostrar}
        
        await update.message.reply_text(f"👋 ¡Bienvenido de vuelta {nombre_mostrar}!")
        await update.message.reply_text("¿Qué vas a enviar?", reply_markup=get_main_keyboard())
        return

    # Nuevo usuario
    user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': None}
    await update.message.reply_text(
        "📚 *Bienvenido al Bot de Cálculo de una variable*\n\n"
        "Para identificarte, escribe tu **nombre completo** o un **código** (ej: EST12345).\n"
        "Así aparecerá en Google Drive.",
        parse_mode='Markdown'
    )
    context.user_data['esperando_identificacion'] = True

async def recibir_identificacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibir nombre/código del estudiante"""
    user_id = update.effective_user.id
    
    if not context.user_data.get('esperando_identificacion'):
        return
    
    texto = update.message.text.strip()
    
    # Guardar en DB
    ok = await save_estudiante(user_id, nombre=texto, codigo=None)
    if not ok:
        await update.message.reply_text("❌ Error al guardar tus datos. Intenta de nuevo.")
        return
    
    # Actualizar sesión
    if user_id in user_sessions:
        user_sessions[user_id]['estudiante'] = texto
    else:
        user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': texto}
    
    context.user_data['esperando_identificacion'] = False
    logger.info(f"✅ Nuevo estudiante registrado: {texto} (ID: {user_id})")
    
    await update.message.reply_text(
        f"✅ Gracias {texto}. ¿Qué vas a enviar?",
        reply_markup=get_main_keyboard()
    )

async def seleccionar_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Seleccionar tipo de entrega (tarea/actividad)"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    tipo = query.data
    
    # Verificar/crear sesión
    if user_id not in user_sessions:
        estudiante = await get_estudiante(user_id)
        if estudiante and (estudiante[0] or estudiante[1]):
            nombre = estudiante[0] if estudiante[0] else estudiante[1]
            user_sessions[user_id] = {'tipo': None, 'fotos': [], 'estudiante': nombre}
        else:
            await query.edit_message_text("Por favor, inicia con /start")
            return
    
    session = user_sessions[user_id]
    session['tipo'] = tipo
    session['fotos'] = []  # Resetear fotos
    
    await query.edit_message_text(
        f"✅ Tipo *{tipo.upper()}* seleccionado.\n\n"
        "Ahora envía *una o varias fotos* de tu trabajo.\n"
        "Cuando termines, presiona **Finalizar entrega**.",
        parse_mode='Markdown',
        reply_markup=get_control_keyboard(has_photos=False)
    )

async def recibir_contenido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibir fotos del usuario"""
    user_id = update.effective_user.id
    
    # Verificar sesión
    if user_id not in user_sessions:
        estudiante = await get_estudiante(user_id)
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
    
    # Descargar a memoria (NO a disco)
    file_bytes = io.BytesIO()
    await file.download_to_memory(file_bytes)
    file_bytes.seek(0)
    
    # Guardar en sesión
    session['fotos'].append({
        'bytes': file_bytes,
        'extension': extension
    })
    
    total = len(session['fotos'])
    await update.message.reply_text(
        f"📸 Recibido. Total: {total}.\n\nPresiona 'Finalizar entrega' cuando termines.",
        reply_markup=get_control_keyboard(has_photos=True)
    )

async def finalizar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finalizar entrega y subir a Drive"""
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
    
    for idx, foto_data in enumerate(session['fotos'], 1):
        nombre_foto = f"pagina_{idx}.jpg"
        
        if upload_photo_to_drive(
            foto_data['bytes'],
            nombre_foto,
            estudiante,
            fecha_str,
            tipo,
            timestamp
        ):
            success += 1
    
    # Limpiar sesión
    session['fotos'] = []
    session['tipo'] = None
    
    await query.message.reply_text(
        f"✅ *Entrega completa*\n\n"
        f"Estudiante: {estudiante}\n"
        f"Tipo: {tipo}\n"
        f"Fecha: {fecha_str}\n"
        f"Subidas: {success}/{total}",
        parse_mode='Markdown'
    )
    
    await query.message.reply_text(
        "¿Otra entrega?",
        reply_markup=get_main_keyboard()
    )

async def cancelar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancelar operación"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id in user_sessions:
        user_sessions[user_id] = {
            'tipo': None,
            'fotos': [],
            'estudiante': user_sessions[user_id].get('estudiante')
        }
    
    await query.edit_message_text("Operación cancelada. Usa /start para empezar de nuevo.")

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje por defecto"""
    await update.message.reply_text("⚠️ Por favor, usa /start para comenzar.")
