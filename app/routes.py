from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, current_app
from app import mongo
from bson import ObjectId
from ddgs import DDGS
from datetime import datetime, timedelta
from app.sockets_utils import notificar_tarea_a_usuario


import os

main = Blueprint('main', __name__)

@main.route("/sw.js")
def service_worker():
    return current_app.send_static_file("sw.js")


@main.route('/')
def index():
    if "user" not in session:
        return redirect(url_for("auth.login"))
    users = list(mongo.db.users.find())
    vapid_public_key = current_app.config["VAPID_PUBLIC_KEY"]
    return render_template("index.html", users=users, vapid_public_key=vapid_public_key)

@main.route('/users_cards')
def users_cards():
    users = list(mongo.db.users.find())
    return render_template('components/cards_fragment.html', users=users)

@main.route('/user_card/<user_id>')
def user_card(user_id):
    user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        return "Usuario no encontrado", 404
    return render_template("components/user_card.html", user=user)

@main.route('/tareas')
def tareas():
    if "user" not in session:
        return redirect(url_for("auth.login"))
    users = list(mongo.db.users.find())
    for user in users:
        user['_id'] = str(user['_id'])
    tareas_por_usuario = {str(user["_id"]): user.get("tareas", []) for user in users}
    vapid_public_key = current_app.config["VAPID_PUBLIC_KEY"]
    return render_template("tareas.html", tareas_por_usuario=tareas_por_usuario, users=users, vapid_public_key=vapid_public_key)

@main.route("/calendario")
def calendario():
    if "user" not in session:
        return redirect(url_for("auth.login"))
    users = list(mongo.db.users.find())
    for user in users:
        user['_id'] = str(user['_id'])

    eventos = []
    for user in users:
        nombre = user.get("nombre", "Sin nombre")
        tareas = user.get("tareas", [])
        for tarea in tareas:
            eventos.append({
                "title": f"{tarea.get('titulo', '')} - {nombre}",
                "start": tarea.get("due_date"),
                "allDay": True
            })

    vapid_public_key = current_app.config["VAPID_PUBLIC_KEY"]
    return render_template("calendario.html", users=users, events=eventos, vapid_public_key=vapid_public_key)

@main.route('/configuracion')
def configuracion():
    if "user" not in session:
        return redirect(url_for("auth.login"))
    users = list(mongo.db.users.find())
    for user in users:
        user['_id'] = str(user['_id'])
    return render_template("configuracion.html", users=users)

def get_food_image(query):
    with DDGS() as ddgs:
        results = ddgs.images(query + " comida plato", max_results=1)
        if results and isinstance(results, list):
            return results[0].get("image", "/static/img/default_food.jpg")
        return "/static/img/default_food.jpg"

@main.route('/menus')
def mostrar_menus():
    if "user" not in session:
        return redirect(url_for("auth.login"))
    dias = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']
    menus_data = {dia: {} for dia in dias}

    for menu in mongo.db.menus.find({}):
        dia = menu.get('dia')
        momento = menu.get('momento')
        titulo = menu.get('titulo')
        imagen = menu.get('img') or get_food_image(titulo) if titulo else None
        asignado = menu.get('asignaciones', {}).get(momento)

        if dia in menus_data:
            menus_data[dia][momento] = {
                "titulo": titulo,
                "img": imagen,
                "asignado": asignado
            }

    users = list(mongo.db.users.find({}, {"nombre": 1}))
    return render_template("menus.html", menus=menus_data, users=users)

@main.route('/api/add_menu', methods=['POST'])
def add_menu():
    if "user" not in session:
        return redirect(url_for("auth.login"))
    data = request.get_json()

    existing = mongo.db.menus.find_one({
        "dia": data["dia"],
        "momento": data["momento"]
    })

    if existing and "img" in existing:
        image_url = existing["img"]
    else:
        try:
            image_url = get_food_image(data["titulo"])
        except:
            image_url = "/static/img/default_food.jpg"

    mongo.db.menus.update_one(
        {"dia": data['dia'], "momento": data['momento']},
        {"$set": {
            "titulo": data['titulo'],
            "img": image_url
        }},
        upsert=True
    )
    return jsonify({"status": "ok"})

@main.route('/api/reset_menus', methods=['DELETE'])
def reset_menus():
    if "user" not in session:
        return redirect(url_for("auth.login"))
    mongo.db.menus.delete_many({})
    return jsonify({"status": "reseteado"})

def get_fecha_real_desde_dia_semana(dia_nombre):
    dias = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']
    hoy = datetime.now()
    dia_actual = hoy.weekday()
    indice_dia = dias.index(dia_nombre)
    diferencia = (indice_dia - dia_actual + 7) % 7
    fecha_objetivo = hoy + timedelta(days=diferencia)
    return fecha_objetivo.strftime("%Y-%m-%d")

@main.route("/api/asignar_comida", methods=["POST"])
def asignar_comida():
    if "user" not in session:
        return jsonify({"error": "No autorizado"}), 401

    data = request.json
    dia = data.get("dia")
    tipo = data.get("tipo")
    miembro = data.get("miembro")

    mongo.db.menus.update_one(
        {"dia": dia},
        {"$set": {f"asignaciones.{tipo}": miembro}},
        upsert=True
    )

    menu = mongo.db.menus.find_one({"dia": dia})
    if not menu:
        return jsonify({"error": "Menú no encontrado"}), 404

    titulo = menu.get("titulo", "Comida asignada")
    momento = tipo.capitalize()
    tarea_titulo = f"Preparar {momento}: {titulo}"
    fecha_real = get_fecha_real_desde_dia_semana(dia)

    mongo.db.users.update_many(
        {},
        {"$pull": {
            "tareas": {
                "titulo": tarea_titulo,
                "due_date": fecha_real
            }
        }}
    )

    mongo.db.tareas.delete_many({
        "titulo": tarea_titulo,
        "due_date": fecha_real
    })

    nueva_tarea = {
        "titulo": tarea_titulo,
        "due_date": fecha_real,
        "pasos": f"Encargado de preparar la {momento.lower()} del día {dia}"
    }

    mongo.db.users.update_one(
        {"nombre": miembro},
        {"$push": {"tareas": nueva_tarea}}
    )

    mongo.db.tareas.insert_one({
        "usuario": miembro,
        **nueva_tarea
    })

    return jsonify({"status": "ok", "tarea_creada": True})

@main.route("/lista_compra")
def lista_compra():
    if "user" not in session:
        return redirect(url_for("auth.login"))
    items = list(mongo.db.lista_compra.find())
    return render_template("lista_compra.html", items=items)

@main.route("/api/test-push/<username>")
def test_push(username):
    from pywebpush import webpush, WebPushException
    from flask import current_app
    import json

    subscripcion = mongo.db.subscriptions.find_one({"usuario": username})
    if not subscripcion:
        return jsonify({"error": f"No se encontró subscripción para {username}"}), 404

    try:
        webpush(
            subscription_info=subscripcion,
            data=json.dumps({
                "title": f"🔔 Test push a {username}",
                "body": f"Hola {username}, esta es una notificación de prueba."
            }),
            vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
            vapid_claims=current_app.config["VAPID_CLAIMS"]
        )
        print(f"📲 Test push enviada a {username}")
        return jsonify({"status": "ok", "message": f"Push enviada a {username}"})

    except WebPushException as ex:
        print(f"❌ Error al enviar push: {repr(ex)}")
        return jsonify({"error": "Fallo al enviar push", "details": str(ex)}), 500
