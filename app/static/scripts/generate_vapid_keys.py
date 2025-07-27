from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64

# Generar clave privada y pública
private_key = ec.generate_private_key(ec.SECP256R1())
public_key = private_key.public_key()

# Clave privada en PEM (opcional, si quieres guardarla también en archivo)
vapid_private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

# Clave pública en PEM (opcional, para archivo)
vapid_public_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)

# Clave privada en bytes crudos para usar en base64 URL-safe
raw_private = private_key.private_numbers().private_value.to_bytes(32, "big")
vapid_private_b64 = base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode("utf-8")

# Clave pública en base64 URL-safe
vapid_public_der = public_key.public_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)
vapid_public_b64 = base64.urlsafe_b64encode(vapid_public_der).rstrip(b"=").decode("utf-8")

# Imprimir todo
print("PRIVATE KEY PEM:\n", vapid_private_pem.decode())
print("PUBLIC KEY PEM:\n", vapid_public_pem.decode())
print("PRIVATE KEY BASE64 (URL-safe):\n", vapid_private_b64)
print("PUBLIC KEY BASE64 (URL-safe):\n", vapid_public_b64)
