import io
import logging
import re
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.auth.transport.requests import Request as GoogleAuthRequest
from config import OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REFRESH_TOKEN, GOOGLE_DRIVE_FOLDER_ID
from functools import lru_cache

logger = logging.getLogger(__name__)

def sanitize_folder_name(name: str) -> str:
    """Sanitizar nombre de carpeta para Google Drive"""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name.strip()[:100]

@lru_cache(maxsize=1)
def get_drive_service():
    """Obtener servicio de Drive con credenciales cacheadas"""
    try:
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
        
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        logger.info("✅ Servicio de Google Drive inicializado")
        return service
    except Exception as e:
        logger.error(f"❌ Error inicializando Drive: {e}", exc_info=True)
        raise

def get_or_create_folder(drive_service, folder_name: str, parent_id: str) -> str:
    """Obtener o crear carpeta en Drive"""
    safe_name = sanitize_folder_name(folder_name)
    
    query = f"name='{safe_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    response = drive_service.files().list(
        q=query, 
        spaces='drive', 
        fields='files(id, name)',
        supportsAllDrives=False
    ).execute()
    
    folders = response.get('files', [])
    
    if folders:
        return folders[0]['id']
    else:
        file_metadata = {
            'name': safe_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        folder = drive_service.files().create(
            body=file_metadata, 
            fields='id',
            supportsAllDrives=False
        ).execute()
        
        return folder['id']

def upload_photo_to_drive(
    file_bytes: io.BytesIO,
    file_name: str,
    student_name: str,
    date_str: str,
    tipo: str,
    timestamp: str
) -> bool:
    """Subir foto a Drive desde memoria (BytesIO)"""
    try:
        drive_service = get_drive_service()
        safe_student_name = sanitize_folder_name(student_name)
        
        # Crear estructura de carpetas: Estudiante/Fecha/Tipo_Timestamp
        parent_id = GOOGLE_DRIVE_FOLDER_ID
        parent_id = get_or_create_folder(drive_service, safe_student_name, parent_id)
        parent_id = get_or_create_folder(drive_service, date_str, parent_id)
        folder_name = f"{tipo}_{timestamp}"
        parent_id = get_or_create_folder(drive_service, folder_name, parent_id)
        
        # Subir archivo desde memoria
        file_bytes.seek(0)  # Resetear pointer al inicio
        media = MediaIoBaseUpload(
            file_bytes,
            mimetype='image/jpeg',
            resumable=True,
            chunksize=1024*1024  # 1MB chunks
        )
        
        file_metadata = {
            'name': file_name,
            'parents': [parent_id]
        }
        
        drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id',
            supportsAllDrives=False
        ).execute()
        
        logger.info(f"✅ Subido a Drive: {safe_student_name}/{date_str}/{folder_name}/{file_name}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error subiendo a Drive: {e}", exc_info=True)
        return False
