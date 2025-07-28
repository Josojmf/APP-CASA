from flask import Flask
from flask_pymongo import PyMongo
from flask_cors import CORS
from flask_socketio import SocketIO
from dotenv import load_dotenv
import os

# Mover importación del blueprint después de definir mongo y app
# from app.auth import auth

# Cargar variables desde .env
load_dotenv()

mongo = PyMongo()
socketio = SocketIO(cors_allowed_origins="*")

def create_app():
    app = Flask(__name__, static_url_path='/static')
    app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")

    # Leer usuario y password desde .env
    mongo_user = os.getenv("MONGO_USER")
    mongo_pass = os.getenv("MONGO_PASS")

    # Leer claves VAPID directamente desde .env
    VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY")
    VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")

    VAPID_CLAIMS = {
        "sub": "mailto:joso.jmf@gmail.com"
    }

    # Datos del cluster
    cluster = "final.yzzh9ig.mongodb.net"
    dbname = "house_app"

    # Construir URI completa
    mongo_uri = f"mongodb+srv://{mongo_user}:{mongo_pass}@{cluster}/{dbname}?retryWrites=true&w=majority&appName=Final"

    app.config["MONGO_URI"] = mongo_uri
    app.config["VAPID_PUBLIC_KEY"] = VAPID_PUBLIC_KEY
    app.config["VAPID_PRIVATE_KEY"] = VAPID_PRIVATE_KEY
    app.config["VAPID_CLAIMS"] = VAPID_CLAIMS

    mongo.init_app(app)
    CORS(app)
    socketio.init_app(app)

    # Importar blueprints después de inicializar mongo
    from app.routes import main
    from app.api import api
    from app.auth import auth

    app.register_blueprint(main)
    app.register_blueprint(auth)
    app.register_blueprint(api)

    @app.context_processor
    def inject_vapid_key():
        return dict(vapid_public_key=VAPID_PUBLIC_KEY)

    return app