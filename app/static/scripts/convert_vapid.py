import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

# Cargar clave privada PEM desde archivo
with open("./vapid_private.pem", "rb") as f:
    private_key_pem = f.read()

private_key = serialization.load_pem_private_key(private_key_pem, password=None)
private_numbers = private_key.private_numbers()
private_value = private_numbers.private_value.to_bytes(32, "big")

# Convertir a base64url (sin padding)
private_key_b64url = base64.urlsafe_b64encode(private_value).rstrip(b"=").decode()

print("PRIVATE KEY BASE64URL (para .env):")
print(private_key_b64url)

# Obtener clave pública
public_key = private_key.public_key()
public_numbers = public_key.public_numbers()

x = public_numbers.x.to_bytes(32, "big")
y = public_numbers.y.to_bytes(32, "big")

# Puntos x e y concatenados con byte 0x04 delante
public_key_raw = b"\x04" + x + y

public_key_b64url = base64.urlsafe_b64encode(public_key_raw).rstrip(b"=").decode()

print("PUBLIC KEY BASE64URL (para .env):")
print(public_key_b64url)
