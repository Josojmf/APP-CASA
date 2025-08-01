from flask import request  # ✅ Añadir import de request

from app import create_app, socketio
from app.globals import user_sockets  # importar aquí, no en sockets_utils

app = create_app()


@socketio.on("connect")
def handle_connect():
    print("Cliente conectado")


@socketio.on("registrar_usuario")
def registrar_usuario(data):
    username = data.get("username")
    if username:
        user_sockets[username] = request.sid
        print(f"✅ Usuario registrado: {username} con sid {request.sid}")


@socketio.on("disconnect")
def handle_disconnect():
    disconnected_sid = request.sid
    for user, sid in list(user_sockets.items()):
        if sid == disconnected_sid:
            del user_sockets[user]
            print(f"❌ Usuario desconectado: {user}")
            break


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
