import os

from pymongo import MongoClient

# Conexión a tu MongoDB local o Atlas
MONGO_PASS = "XyGItdDKpWkfJfjT"
MONGO_USER = "joso"
cluster = "final.yzzh9ig.mongodb.net"
dbname = "house_app"
mongo_uri = f"mongodb+srv://{MONGO_USER}:{MONGO_PASS}@{cluster}/{dbname}?retryWrites=true&w=majority&appName=Final"
client = MongoClient(mongo_uri)
db = client["house-app"]  # Reemplaza si tu DB se llama distinto

# Colecciones
users = db.users.find()
subs = db.subscriptions.find()

# Mapeo de subscripciones
subs_map = {sub["user"]: sub["subscriptions"] for sub in subs}

print("📋 Estado de subscripciones push por usuario:")
print("===========================================")
for user in users:
    nombre = user["nombre"].lower()
    user_subs = subs_map.get(nombre, [])
    estado = (
        f"{len(user_subs)} subscripción/es ✅" if user_subs else "❌ Sin subscripciones"
    )
    print(f"👤 {nombre}: {estado}")
