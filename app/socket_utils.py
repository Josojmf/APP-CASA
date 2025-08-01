from app import socketio, mongo
from app.globals import user_sockets
from flask import current_app, session
from datetime import datetime
import json
from pywebpush import webpush, WebPushException
from urllib.parse import urlparse
import logging

# Configure logging
logger = logging.getLogger(__name__)

# ============================================================
#   FUNCIONES PUSH MEJORADAS
# ============================================================
def get_vapid_claims(endpoint_url):
    """
    Genera el diccionario de claims VAPID para webpush.
    """
    try:
        parsed = urlparse(endpoint_url)
        return {
            "aud": f"{parsed.scheme}://{parsed.netloc}",
            "sub": "mailto:joso.jmf@gmail.com"
        }
    except Exception as e:
        logger.error(f"Error get_vapid_claims: {e}")
        return None

def send_push_to_all(title, body, url="/", icon="/static/icons/house-icon.png"):
    """Envía notificación push a todos los usuarios con suscripción - MEJORADO."""
    try:
        vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
        if not vapid_private_key:
            logger.error("VAPID_PRIVATE_KEY no configurada")
            return

        subscripciones = mongo.db.subscriptions.find({})
        total_sent = 0

        for sub_doc in subscripciones:
            user_name = sub_doc.get("user", "Unknown")
            for sub in sub_doc.get("subscriptions", []):
                try:
                    endpoint = sub.get("endpoint")
                    if not endpoint:
                        continue
                        
                    claims = get_vapid_claims(endpoint)
                    if not claims:
                        continue

                    webpush(
                        subscription_info=sub,
                        data=json.dumps({
                            "title": title,
                            "body": body,
                            "icon": icon,
                            "badge": icon,
                            "url": url
                        }),
                        vapid_private_key=vapid_private_key,
                        vapid_claims=claims
                    )
                    total_sent += 1
                    logger.info(f"📲 Push enviado a {user_name}")
                    
                except WebPushException as ex:
                    logger.error(f"❌ Error push a {user_name}: {repr(ex)}")
                    # Limpiar suscripciones inválidas
                    if ex.response and ex.response.status_code in [410, 404]:
                        logger.info("🧹 Eliminando subscripción expirada...")
                        mongo.db.subscriptions.update_one(
                            {"user": user_name},
                            {"$pull": {"subscriptions": sub}}
                        )
                except Exception as e:
                    logger.error(f"❌ Error inesperado enviando push: {e}")

        logger.info(f"📊 Total notificaciones enviadas: {total_sent}")
        
    except Exception as e:
        logger.error(f"❌ Error en send_push_to_all: {e}")

def send_push_to_user(user_name, title, body, url="/", icon="/static/icons/house-icon.png"):
    """Enviar notificación push a un usuario específico - MEJORADO"""
    try:
        vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
        if not vapid_private_key:
            logger.error("VAPID_PRIVATE_KEY no configurada")
            return False

        subscription_doc = mongo.db.subscriptions.find_one({"user": user_name})
        if not subscription_doc:
            logger.warning(f"No hay suscripciones para {user_name}")
            return False

        success_count = 0
        for sub in subscription_doc.get("subscriptions", []):
            try:
                endpoint = sub.get("endpoint")
                if not endpoint:
                    continue
                    
                claims = get_vapid_claims(endpoint)
                if not claims:
                    continue

                webpush(
                    subscription_info=sub,
                    data=json.dumps({
                        "title": title,
                        "body": body,
                        "icon": icon,
                        "url": url
                    }),
                    vapid_private_key=vapid_private_key,
                    vapid_claims=claims
                )
                success_count += 1
                logger.info(f"✅ Notificación enviada a {user_name}")
                
            except WebPushException as ex:
                logger.error(f"❌ Error al enviar push a {user_name}: {repr(ex)}")
                # Limpiar suscripciones inválidas
                if ex.response and ex.response.status_code in [410, 404]:
                    mongo.db.subscriptions.update_one(
                        {"user": user_name},
                        {"$pull": {"subscriptions": sub}}
                    )
            except Exception as e:
                logger.error(f"❌ Error inesperado enviando push: {e}")

        return success_count > 0
        
    except Exception as e:
        logger.error(f"❌ Error en send_push_to_user: {e}")
        return False

# ============================================================
#   NOTIFICACIONES DE TAREAS MEJORADAS
# ============================================================
def notificar_tarea_a_usuario(tarea):
    """Notifica una tarea a un usuario específico - MEJORADO"""
    try:
        username = tarea.get("asignado", "").strip()
        if not username:
            logger.warning("⚠️ Tarea sin asignado")
            return

        # Intentar notificación por socket primero
        sid = user_sockets.get(username.lower())
        
        if sid:
            try:
                socketio.emit("nueva_tarea", tarea, to=sid)
                logger.info(f"✅ Notificada tarea a {username} por socket ({sid})")
            except Exception as e:
                logger.error(f"Error enviando socket a {username}: {e}")

        # Enviar notificación push como respaldo
        success = send_push_to_user(
            user_name=username,
            title="📋 Tarea asignada",
            body=tarea.get("titulo", "Nueva tarea"),
            url="/tareas"
        )
        
        if not success and not sid:
            logger.warning(f"⚠️ No se pudo notificar a {username} por ningún medio")
            
    except Exception as e:
        logger.error(f"Error en notificar_tarea_a_usuario: {e}")

# ============================================================
#   EVENTOS DE CHAT MEJORADOS
# ============================================================
def register_chat_events():
    """Registra eventos de chat en tiempo real con Socket.IO - MEJORADO"""

    @socketio.on("send_message")
    def handle_send_message(data):
        """Maneja el envío de mensajes de chat - MEJORADO"""
        try:
            # Validar datos de entrada
            if not data or not isinstance(data, dict):
                logger.error("Datos de mensaje inválidos")
                return

            # Usuario actual (preferir el que viene del cliente)
            user = data.get("user") or session.get("username") or session.get("user", "Anónimo")
            
            # Foto que viene del cliente o de la sesión
            photo = (data.get("photo") or "").strip() or (session.get("photo") or "").strip()
            
            message = data.get("message", "").strip()
            if not message:
                logger.warning("Mensaje vacío recibido")
                return  # Evitar mensajes vacíos

            # 🔹 Si no tenemos foto, buscarla en Mongo
            if not photo or photo == "/static/images/default-avatar.png":
                try:
                    user_doc = mongo.db.users.find_one({"nombre": user})
                    if user_doc and user_doc.get("imagen"):
                        imagen_data = user_doc["imagen"]
                        if str(imagen_data).startswith("data:image"):
                            photo = imagen_data
                        else:
                            photo = f"data:image/jpeg;base64,{imagen_data}"
                    else:
                        photo = "/static/images/default-avatar.png"
                except Exception as e:
                    logger.error(f"Error obteniendo foto de usuario {user}: {e}")
                    photo = "/static/images/default-avatar.png"

            # Guardar mensaje en MongoDB
            try:
                msg_doc = {
                    "user": user,
                    "photo": photo,
                    "message": message,
                    "timestamp": datetime.utcnow()
                }
                result = mongo.db.messages.insert_one(msg_doc)
                logger.info(f"Mensaje guardado en DB: {result.inserted_id}")
            except Exception as e:
                logger.error(f"Error guardando mensaje en DB: {e}")
                return

            # Emitir mensaje a todos los clientes conectados
            try:
                socketio.emit("chat_message", {
                    "user": msg_doc["user"],
                    "photo": msg_doc["photo"],
                    "message": msg_doc["message"],
                    "timestamp": msg_doc["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                })
                logger.info(f"Mensaje emitido por socket de {user}")
            except Exception as e:
                logger.error(f"Error emitiendo mensaje por socket: {e}")

            # Notificación push a todos los usuarios
            try:
                send_push_to_all(
                    title=f"💬 Mensaje nuevo de {user}",
                    body=message[:100] + ("..." if len(message) > 100 else ""),  # Limitar longitud
                    url="/chat"
                )
            except Exception as e:
                logger.error(f"Error enviando notificación push: {e}")

        except Exception as e:
            logger.error(f"Error general en handle_send_message: {e}")

    @socketio.on("connect")
    def handle_connect():
        """Maneja conexiones de socket - MEJORADO"""
        try:
            user = session.get('user') or session.get('username', 'Anonymous')
            logger.info(f'Usuario {user} conectado por socket')
            
            # Opcional: almacenar el socket ID del usuario
            if user != 'Anonymous':
                user_sockets[user.lower()] = request.sid
                
        except Exception as e:
            logger.error(f'Error en connect handler: {e}')

    @socketio.on("disconnect")
    def handle_disconnect():
        """Maneja desconexiones de socket - CORREGIDO"""
        try:
            user = session.get('user') or session.get('username', 'Unknown')
            logger.info(f'Usuario {user} desconectado')
            
            # Limpiar del diccionario de sockets activos
            if user != 'Unknown' and user.lower() in user_sockets:
                del user_sockets[user.lower()]
                
        except Exception as e:
            logger.error(f'Error en disconnect handler: {e}')

    logger.info("Eventos de chat registrados correctamente")

# ============================================================
#   UTILIDADES ADICIONALES
# ============================================================
def get_active_users():
    """Obtiene lista de usuarios activos por socket"""
    try:
        return list(user_sockets.keys())
    except Exception as e:
        logger.error(f"Error obteniendo usuarios activos: {e}")
        return []

def cleanup_inactive_sockets():
    """Limpia sockets inactivos del diccionario"""
    try:
        # Esta función podría implementarse para limpiar periódicamente
        # sockets que ya no estén activos
        pass
    except Exception as e:
        logger.error(f"Error en cleanup_inactive_sockets: {e}")