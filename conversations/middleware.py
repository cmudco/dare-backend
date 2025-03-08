import jwt
from django.conf import settings
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from urllib.parse import parse_qs
from channels.middleware import BaseMiddleware

class JwtAuthMiddleware(BaseMiddleware):
    """
    Middleware to authenticate WebSocket connections using JWT token from URL query parameters.
    """

    async def __call__(self, scope, receive, send):
        """
        This is the entry point of the custom middleware.
        It extracts the JWT token from the query parameters and authenticates the user.
        """
        query_string = scope.get('query_string', b'').decode('utf-8')
        query_params = parse_qs(query_string)

        jwt_token = query_params.get('jwt_key', [None])[0]
        if jwt_token:
            try:
                decoded_token = jwt.decode(jwt_token, settings.SECRET_KEY, algorithms=['HS256'])

                user_id = decoded_token.get('user_id')
                if user_id:
                    user = await self.get_user(user_id)
                    scope['user'] = user
                else:
                    scope['user'] = None
            except jwt.ExpiredSignatureError:
                scope['user'] = None
            except jwt.DecodeError:
                scope['user'] = None
            except Exception as e:
                scope['user'] = None
        else:
            scope['user'] = None

        return await super().__call__(scope, receive, send)

    @database_sync_to_async
    def get_user(self, user_id):
        """Fetch the user from the database using the user_id from the decoded token."""
        User = get_user_model()
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None