from pymongo import MongoClient
import base64

# Conexión a MongoDB
client = MongoClient("mongodb+srv://joso:XyGItdDKpWkfJfjT@final.yzzh9ig.mongodb.net/house_app?retryWrites=true&w=majority&appName=Final")
db = client["house_app"]

# Función para convertir archivo a base64
def encode_image_to_base64(filepath):
    with open(filepath, "rb") as image_file:
        encoded = base64.b64encode(image_file.read()).decode("utf-8")
    return encoded

# Prepara imágenes
img_joso = encode_image_to_base64("./img/joso.png")
img_ana = encode_image_to_base64("./img/ana.png")
img_papa = encode_image_to_base64("./img/papa.png")
img_mama = encode_image_to_base64("./img/mama.png")

# Datos
users = [
    {
        "nombre": "Joso",
        "encasa": True,
        "tareas": ["Sacar la basura", "Regar las plantas"],
        "calendario": [],
        "imagen": img_joso
    },
    {
        "nombre": "Ana",
        "encasa": False,
        "tareas": ["Hacer la compra", "Pasear al perro"],
        "calendario": [],
        "imagen": img_ana
    },
    {
        "nombre": "Papa",
        "encasa": True,
        "tareas": ["Lavar el coche"],
        "calendario": [],
        "imagen": img_papa
    },
    {
        "nombre": "Mama",
        "encasa": False,
        "tareas": ["Cocinar", "Planchar"],
        "calendario": [],
        "imagen": img_mama
    },
]

# Limpia y reinserta
db.users.delete_many({})
db.users.insert_many(users)

print("Usuarios insertados con imágenes base64 correctamente")
