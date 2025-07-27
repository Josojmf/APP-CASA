from app import create_app, mongo

# Crear el contexto de la app
app = create_app()
with app.app_context():
    mongo.db.subscriptions.delete_many({})
    print("Subscripciones eliminadas")
