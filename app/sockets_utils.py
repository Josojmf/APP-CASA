from app import socketio
from app.globals import user_sockets

def notificar_tarea_a_usuario(tarea):
    username = tarea.get("asignado")
    if not username:
        print("⚠️ Tarea sin asignado")
        return

    sid = user_sockets.get(username)
    if sid:
        socketio.emit("nueva_tarea", tarea, to=sid)
        print(f"✅ Notificada tarea a {username} (socket {sid})")
    else:
        print(f"⚠️ Usuario {username} no conectado")
