from flask import (Blueprint, redirect, render_template, request, session,
                   url_for)

from app import mongo
from app.globals import user_sockets

auth = Blueprint("auth", __name__)


@auth.route("/login")
def login():
    users = list(mongo.db.users.find())
    return render_template("login.html", users=users)


@auth.route("/select_user", methods=["POST"])
def select_user():
    username = request.form.get("username")  # Este viene del formulario HTML

    if username:
        session["user"] = username.lower()  # Guardamos en minúsculas 🔥
        return redirect(url_for("main.index"))
    return redirect(url_for("auth.login"))


@auth.route("/logout")
def logout():
    username = session.get("user")  # estaba mal: era 'user', no 'username'
    session.clear()

    if username in user_sockets:
        del user_sockets[username]
        print(f"[LOGOUT] {username} eliminado de sockets")

    return redirect(url_for("auth.login"))
