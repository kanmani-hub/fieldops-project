import jwt
import datetime

payload = {
    "tenant_id": "**platform**",
    "sub": "local-super-admin",
    "roles": ["super_admin"],
    "exp": datetime.datetime.utcnow() + datetime.timedelta(days=1),
}

token = jwt.encode(
    payload,
    "test-jwt-secret",
    algorithm="HS256",
)

print(token)