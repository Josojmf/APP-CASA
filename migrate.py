from ddgs import DDGS
from app import create_app, mongo
import time


def get_food_image(query):
    with DDGS() as ddgs:
        results = ddgs.images(query + " comida plato", max_results=1)
        if results and isinstance(results, list):
            return results[0].get("image", "/static/img/default_food.jpg")
        return "/static/img/default_food.jpg"


# Inicializa la app Flask y su contexto
app = create_app()

with app.app_context():
    try:
        for menu in mongo.db.menus.find():
            if "img" not in menu:
                print(f"Buscando imagen para: {menu.get('titulo', 'SIN TÍTULO')}")
                try:
                    img_url = get_food_image(menu["titulo"])
                    mongo.db.menus.update_one(
                        {"_id": menu["_id"]}, {"$set": {"img": img_url}}
                    )
                    print("✔️ Imagen añadida")
                except Exception as e:
                    print(f"❌ Error al procesar {menu['titulo']}: {e}")
                time.sleep(2)  # <- Esto ayuda con el rate-limit
    finally:
        mongo.cx.close()
