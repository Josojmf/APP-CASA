from flask import Blueprint, render_template, request, redirect, session, url_for
from app import mongo

auth = Blueprint("auth", __name__)

@auth.route('/login')
def login():
    users = list(mongo.db.users.find())
    return render_template("login.html", users=users)

@auth.route('/select_user', methods=['POST'])
def select_user():
    username = request.form.get('username')
    if username:
        session['user'] = username
        return redirect(url_for('main.index'))
    return redirect(url_for('auth.login'))

@auth.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('auth.login'))
