from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, current_app
from app import mongo
from bson import ObjectId
from datetime import datetime, timedelta
from functools import wraps
import os
import traceback
import requests
from ddgs import DDGS
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)

# ==========================================
# Decorador para autenticación
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# Utilidades
# ==========================================
def get_food_image(food_name):
    """
    Obtener imagen de comida usando DuckDuckGo
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(
                keywords=f"{food_name} food recipe",
                region="es-es",
                safesearch="moderate",
                size="medium",
                max_results=1
            ))
            if results:
                return results[0].get("image", "/static/img/default_food.jpg")
    except Exception as e:
        logger.error(f"Error get_food_image: {e}")
    
    return "/static/img/default_food.jpg"

def get_fecha_real_desde_dia_semana(dia_nombre):
    """
    Convertir nombre de día a fecha real de esta semana
    """
    dias = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']
    try:
        hoy = datetime.now()
        dia_actual = hoy.weekday()
        indice_dia = dias.index(dia_nombre)
        diferencia = (indice_dia - dia_actual + 7) % 7
        fecha_objetivo = hoy + timedelta(days=diferencia)
        return fecha_objetivo.strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d")

# ==========================================
# Rutas principales
# ==========================================
@main.route("/health")
def health_check():
    """Health check endpoint para Docker y tests"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }), 200


@main.route("/sw.js")
def service_worker():
    """Servir el service worker"""
    return current_app.send_static_file("sw.js")

@main.route('/')
@login_required
def index():
    """Página principal con usuarios"""
    try:
        users = list(mongo.db.users.find())
        vapid_public_key = current_app.config.get("VAPID_PUBLIC_KEY", "")
        
        # Estadísticas básicas
        stats = {
            "total_users": len(users),
            "users_at_home": len([u for u in users if u.get('encasa', False)]),
            "total_tasks": sum(len(u.get("tareas", [])) for u in users)
        }
        
        return render_template("index.html", 
                             users=users, 
                             vapid_public_key=vapid_public_key,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error index: {e}")
        return "Error interno del servidor", 500

@main.route('/users_cards')
@login_required
def users_cards():
    """Fragmento HTMX para las tarjetas de usuarios"""
    try:
        users = list(mongo.db.users.find())
        return render_template('components/cards_fragment.html', users=users)
    except Exception as e:
        logger.error(f"Error users_cards: {e}")
        return "Error al cargar usuarios", 500

@main.route('/user_card/<user_id>')
@login_required
def user_card(user_id):
    """Tarjeta individual de usuario"""
    try:
        user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return "Usuario no encontrado", 404
        return render_template("components/user_card.html", user=user)
    except Exception as e:
        logger.error(f"Error user_card: {e}")
        return "Error al cargar usuario", 500

@main.route('/tareas')
@login_required
def tareas():
    """Página de gestión de tareas"""
    try:
        users = list(mongo.db.users.find())
        for user in users:
            user['_id'] = str(user['_id'])
            # Ordenar tareas por fecha de vencimiento
            tareas = user.get('tareas', [])
            if tareas:
                tareas.sort(key=lambda x: x.get('due_date', '9999-12-31'))
        
        vapid_public_key = current_app.config.get("VAPID_PUBLIC_KEY", "")
        
        # Estadísticas de tareas
        total_tasks = sum(len(user.get("tareas", [])) for user in users)
        overdue_tasks = 0
        today = datetime.now().strftime('%Y-%m-%d')
        
        for user in users:
            for tarea in user.get('tareas', []):
                if tarea.get('due_date', '9999-12-31') < today:
                    overdue_tasks += 1
        
        stats = {
            "total_tasks": total_tasks,
            "overdue_tasks": overdue_tasks
        }
        
        return render_template("tareas.html", 
                             users=users, 
                             vapid_public_key=vapid_public_key,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error tareas: {e}")
        return "Error al cargar tareas", 500

@main.route("/calendario")
@login_required
def calendario():
    """Página del calendario con eventos"""
    try:
        users = list(mongo.db.users.find())
        for user in users:
            user['_id'] = str(user['_id'])

        eventos = []
        for user in users:
            nombre = user.get("nombre", "Sin nombre")
            tareas = user.get("tareas", [])
            for tarea in tareas:
                # Determinar color según prioridad
                color_map = {
                    'urgente': '#ef4444',
                    'alta': '#f97316', 
                    'normal': '#f59e0b',
                    'baja': '#22c55e'
                }
                color = color_map.get(tarea.get('prioridad', 'normal'), '#10b981')
                
                eventos.append({
                    "title": f"{tarea.get('titulo', '')} - {nombre}",
                    "start": tarea.get("due_date"),
                    "allDay": True,
                    "backgroundColor": color,
                    "borderColor": color,
                    "extendedProps": {
                        "asignee": nombre,
                        "prioridad": tarea.get('prioridad', 'normal'),
                        "pasos": tarea.get('pasos', ''),
                        "user_id": str(user['_id'])
                    }
                })

        vapid_public_key = current_app.config.get("VAPID_PUBLIC_KEY", "")
        return render_template("calendario.html", 
                             users=users, 
                             events=eventos, 
                             vapid_public_key=vapid_public_key)
    except Exception as e:
        logger.error(f"Error calendario: {e}")
        return "Error al cargar calendario", 500

@main.route('/menus')
@login_required
def mostrar_menus():
    """Página de gestión de menús"""
    try:
        dias = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']
        menus_data = {dia: {'comida': None, 'cena': None} for dia in dias}

        # Cargar menús existentes
        for menu in mongo.db.menus.find({}):
            dia = menu.get('dia')
            momento = menu.get('momento')
            titulo = menu.get('titulo')
            imagen = menu.get('img')
            asignaciones = menu.get('asignaciones', {})
            asignado = asignaciones.get(momento) if asignaciones else None

            if dia in menus_data and momento in ['comida', 'cena']:
                menus_data[dia][momento] = {
                    "titulo": titulo,
                    "img": imagen,
                    "asignado": asignado
                }

        users = list(mongo.db.users.find({}, {"nombre": 1}))
        
        # Estadísticas de menús
        total_menus = sum(1 for dia_data in menus_data.values() 
                         for momento_data in dia_data.values() 
                         if momento_data and momento_data.get('titulo'))
        assigned_menus = sum(1 for dia_data in menus_data.values() 
                           for momento_data in dia_data.values() 
                           if momento_data and momento_data.get('asignado'))
        
        stats = {
            "total_menus": total_menus,
            "assigned_menus": assigned_menus,
            "completion_percentage": int((assigned_menus / max(total_menus, 1)) * 100)
        }
        
        return render_template("menus.html", 
                             menus=menus_data, 
                             users=users,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error mostrar_menus: {e}")
        return "Error al cargar menús", 500

@main.route('/api/add_menu', methods=['POST'])
@login_required
def add_menu():
    """Añadir nuevo menú"""
    try:
        data = request.get_json()
        dia = data.get("dia")
        momento = data.get("momento")
        titulo = data.get("titulo")

        if not dia or not momento or not titulo:
            return jsonify({"error": "Faltan campos obligatorios"}), 400

        # Verificar si ya existe
        existing = mongo.db.menus.find_one({
            "dia": dia,
            "momento": momento
        })

        # Obtener imagen
        if existing and existing.get("img"):
            image_url = existing["img"]
        else:
            try:
                image_url = get_food_image(titulo)
            except Exception as e:
                logger.error(f"Error get_food_image: {e}")
                image_url = "/static/img/default_food.jpg"

        # Actualizar o crear menú
        mongo.db.menus.update_one(
            {"dia": dia, "momento": momento},
            {"$set": {
                "titulo": titulo,
                "img": image_url,
                "updated_by": session.get('user'),
                "updated_at": datetime.now()
            }},
            upsert=True
        )
        
        return jsonify({"status": "ok", "image": image_url})
    except Exception as e:
        logger.error(f"Error add_menu: {e}")
        return jsonify({"error": "Error al añadir menú"}), 500

@main.route('/api/reset_menus', methods=['DELETE'])
@login_required
def reset_menus():
    """Reiniciar todos los menús"""
    try:
        result = mongo.db.menus.delete_many({})
        
        # También eliminar tareas de cocina relacionadas
        mongo.db.users.update_many(
            {},
            {"$pull": {"tareas": {"titulo": {"$regex": "^Preparar (Comida|Cena):"}}}}
        )
        
        return jsonify({
            "status": "reseteado", 
            "deleted_count": result.deleted_count
        })
    except Exception as e:
        logger.error(f"Error reset_menus: {e}")
        return jsonify({"error": "Error al reiniciar menús"}), 500

@main.route("/api/asignar_comida", methods=["POST"])
@login_required
def asignar_comida():
    """Asignar persona responsable de preparar comida"""
    try:
        data = request.json
        dia = data.get("dia")
        tipo = data.get("tipo")  # 'comida' o 'cena'
        miembro = data.get("miembro")

        if not dia or not tipo or not miembro:
            return jsonify({"error": "Faltan campos obligatorios"}), 400

        # Verificar que el usuario existe
        user = mongo.db.users.find_one({"nombre": miembro})
        if not user:
            return jsonify({"error": "Usuario no encontrado"}), 404

        # Actualizar asignación en menús
        mongo.db.menus.update_one(
            {"dia": dia},
            {"$set": {
                f"asignaciones.{tipo}": miembro,
                "assigned_by": session.get('user'),
                "assigned_at": datetime.now()
            }},
            upsert=True
        )

        # Obtener información del menú para crear tarea
        menu = mongo.db.menus.find_one({"dia": dia})
        titulo_menu = "Comida sin especificar"
        
        if menu and menu.get("titulo"):
            titulo_menu = menu["titulo"]

        momento = tipo.capitalize()
        tarea_titulo = f"Preparar {momento}: {titulo_menu}"
        fecha_real = get_fecha_real_desde_dia_semana(dia)

        # Eliminar tareas previas de este menú
        mongo.db.users.update_many(
            {},
            {"$pull": {
                "tareas": {
                    "titulo": tarea_titulo,
                    "due_date": fecha_real
                }
            }}
        )

        # Crear nueva tarea
        nueva_tarea = {
            "titulo": tarea_titulo,
            "due_date": fecha_real,
            "pasos": f"Encargado de preparar la {tipo} del día {dia}",
            "prioridad": "normal",
            "created_by": session.get('user'),
            "created_at": datetime.now(),
            "tipo": "cocina",
            "asignado": miembro  # Agregar campo asignado
        }

        # Asignar tarea al usuario
        mongo.db.users.update_one(
            {"nombre": miembro},
            {"$push": {"tareas": nueva_tarea}}
        )

        return jsonify({
            "status": "ok", 
            "tarea_creada": True,
            "tarea_titulo": tarea_titulo
        })
    except Exception as e:
        logger.error(f"Error asignar_comida: {e}")
        return jsonify({"error": "Error al asignar comida"}), 500

@main.route("/lista_compra")
@login_required
def lista_compra():
    """Página de lista de la compra"""
    try:
        items = list(mongo.db.lista_compra.find().sort("created_at", -1))
        
        # Estadísticas
        total_items = len(items)
        completed_items = len([item for item in items if item.get('comprado', False)])
        
        stats = {
            "total_items": total_items,
            "completed_items": completed_items,
            "pending_items": total_items - completed_items,
            "completion_percentage": int((completed_items / max(total_items, 1)) * 100)
        }
        
        return render_template("lista_compra.html", 
                             items=items,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error lista_compra: {e}")
        return "Error al cargar lista de compra", 500

@main.route('/configuracion')
@login_required
def configuracion():
    """Página de configuración"""
    try:
        users = list(mongo.db.users.find())
        for user in users:
            user['_id'] = str(user['_id'])
        
        # Estadísticas del sistema
        total_subscriptions = mongo.db.subscriptions.count_documents({})
        total_completed_tasks = mongo.db.completed_tasks.count_documents({})
        
        system_stats = {
            "total_users": len(users),
            "total_subscriptions": total_subscriptions,
            "total_completed_tasks": total_completed_tasks,
            "current_theme": session.get('theme', 'light')
        }
        
        return render_template("configuracion.html", 
                             users=users,
                             system_stats=system_stats)
    except Exception as e:
        logger.error(f"Error configuracion: {e}")
        return "Error al cargar configuración", 500

@main.route('/api/add_user', methods=['POST'])
@login_required
def add_user():
    """Añadir nuevo usuario"""
    try:
        data = request.json
        nombre = data.get("nombre", "").strip()
        imagen = data.get("imagen", "")

        if not nombre:
            return jsonify({"error": "El nombre es obligatorio"}), 400

        # Verificar si ya existe
        if mongo.db.users.find_one({"nombre": {"$regex": f"^{nombre}$", "$options": "i"}}):
            return jsonify({"error": "Ya existe un usuario con ese nombre"}), 409

        # Procesar imagen
        if imagen and imagen.startswith("data:image/"):
            imagen = imagen.split(",")[1]
        
        if imagen:
            imagen = imagen.strip()

        # Crear usuario
        new_user = {
            "nombre": nombre,
            "encasa": True,
            "imagen": imagen,
            "tareas": [],
            "calendario": [],
            "created_by": session.get('user'),
            "created_at": datetime.now()
        }

        result = mongo.db.users.insert_one(new_user)
        
        return jsonify({
            "success": True, 
            "user_id": str(result.inserted_id)
        }), 201
    except Exception as e:
        logger.error(f"Error add_user: {e}")
        return jsonify({"error": "Error al crear usuario"}), 500

@main.route('/api/delete_user/<user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    """Eliminar usuario"""
    try:
        # Verificar permisos (solo Joso puede eliminar usuarios)
        if session.get('user') != 'Joso':
            return jsonify({"error": "Permisos insuficientes"}), 403

        obj_id = ObjectId(user_id)
        
        # Obtener información del usuario antes de eliminar
        user = mongo.db.users.find_one({"_id": obj_id})
        if not user:
            return jsonify({"error": "Usuario no encontrado"}), 404

        # Eliminar usuario
        mongo.db.users.delete_one({"_id": obj_id})
        
        # Limpiar suscripciones relacionadas
        mongo.db.subscriptions.delete_many({"user": user.get("nombre")})
        
        # Limpiar tareas completadas relacionadas
        mongo.db.completed_tasks.delete_many({"completed_by": user.get("nombre")})
        
        return jsonify({"success": True}), 200
    except Exception as e:
        logger.error(f"Error delete_user: {e}")
        return jsonify({"error": "Error al eliminar usuario"}), 500

@main.route('/api/toggle_theme', methods=['POST'])
@login_required
def toggle_theme():
    """Cambiar tema de la aplicación"""
    try:
        data = request.json
        new_theme = data.get("theme")
        
        if new_theme not in ['light', 'dark']:
            return jsonify({"error": "Tema inválido"}), 400
            
        session["theme"] = new_theme
        return jsonify({"success": True, "theme": new_theme})
    except Exception as e:
        logger.error(f"Error toggle_theme: {e}")
        return jsonify({"error": "Error al cambiar tema"}), 500

@main.route("/chat")
@login_required
def chat():
    """Página de chat familiar"""
    try:
        # Recuperar últimos mensajes
        messages = list(mongo.db.messages.find().sort("timestamp", -1).limit(50))
        messages.reverse()
        
        for m in messages:
            m["_id"] = str(m["_id"])
            if m.get("timestamp"):
                m["timestamp"] = m["timestamp"].strftime("%Y-%m-%d %H:%M:%S")

        # Estadísticas del chat
        total_messages = mongo.db.messages.count_documents({})
        today_messages = mongo.db.messages.count_documents({
            "timestamp": {"$gte": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        
        stats = {
            "total_messages": total_messages,
            "today_messages": today_messages
        }

        return render_template("chat.html", 
                             messages=messages,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error chat: {e}")
        return "Error al cargar chat", 500

@main.route("/chat/messages", methods=["GET"])
@login_required
def get_chat_messages():
    """API para obtener mensajes del chat"""
    try:
        # Asegurar índice para mejorar rendimiento
        mongo.db.messages.create_index("timestamp")

        # Obtener últimos mensajes
        limit = int(request.args.get('limit', 50))
        messages = list(mongo.db.messages.find().sort("timestamp", -1).limit(limit))
        messages.reverse()

        # Formatear para JSON
        for m in messages:
            m["_id"] = str(m["_id"])
            if m.get("timestamp"):
                m["timestamp"] = m["timestamp"].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify(messages), 200
    except Exception as e:
        logger.error(f"Error get_chat_messages: {e}")
        return jsonify({"error": "Error al obtener mensajes"}), 500

@main.route("/asistente-familiar")
@login_required
def asistente_familiar_page():
    """Página del asistente familiar con IA"""
    try:
        # Estadísticas del asistente
        chat_count = len(session.get("chat_history", []))
        
        stats = {
            "messages_in_session": chat_count,
            "ai_available": bool(os.getenv("GROQ_API_KEY"))
        }
        
        return render_template("ai.html", stats=stats)
    except Exception as e:
        logger.error(f"Error asistente_familiar_page: {e}")
        return "Error al cargar asistente", 500

@main.route("/api/test-push/<username>")
@login_required
def test_push(username):
    """Endpoint para probar notificaciones push - MEJORADO"""
    try:
        # Solo admin puede probar push
        if session.get('user') != 'Joso':
            return jsonify({"error": "Permisos insuficientes"}), 403

        from pywebpush import webpush, WebPushException
        from urllib.parse import urlparse
        import json

        subscripcion = mongo.db.subscriptions.find_one({"user": username})
        if not subscripcion:
            return jsonify({"error": f"No se encontró suscripción para {username}"}), 404

        vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
        if not vapid_private_key:
            return jsonify({"error": "VAPID no configurado"}), 500

        def get_vapid_claims(endpoint_url):
            try:
                parsed = urlparse(endpoint_url)
                return {
                    "aud": f"{parsed.scheme}://{parsed.netloc}",
                    "sub": "mailto:joso.jmf@gmail.com"
                }
            except Exception:
                return None

        success_count = 0
        for sub in subscripcion.get("subscriptions", []):
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
                        "title": f"🔔 Test push a {username}",
                        "body": f"Hola {username}, esta es una notificación de prueba desde House App.",
                        "icon": "/static/icons/house-icon.png",
                        "url": "/"
                    }),
                    vapid_private_key=vapid_private_key,
                    vapid_claims=claims
                )
                success_count += 1
                logger.info(f"📲 Test push enviada a {username}")
            except WebPushException as ex:
                logger.error(f"❌ Error al enviar push: {repr(ex)}")
                return jsonify({
                    "error": "Fallo al enviar push", 
                    "details": str(ex)
                }), 500

        return jsonify({
            "status": "ok", 
            "message": f"Push enviada a {username}",
            "sent_count": success_count
        })
    except Exception as e:
        logger.error(f"Error test_push: {e}")
        return jsonify({"error": "Error interno"}), 500

# ==========================================
# Manejo de errores mejorado
# ==========================================
@main.errorhandler(404)
def not_found_error(error):
    logger.error(f"404 Error: {request.url}")
    return render_template('errors/404.html'), 404

@main.errorhandler(500)
def internal_error(error):
    logger.error(f"500 Error: {error}")
    return render_template('errors/500.html'), 500

@main.errorhandler(403)
def forbidden_error(error):
    logger.error(f"403 Error: {error}")
    return render_template('errors/403.html'), 403