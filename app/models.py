class PurchaseHistory:
    def __init__(self, items, date=None):
        self.items = items  # Lista de productos
        self.date = date or datetime.now()  # Fecha de la compra

    def to_dict(self):
        return {"items": self.items, "date": self.date.isoformat()}
