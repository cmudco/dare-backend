from syftbox.errors import SyftBoxErrorCode, SyftBoxException
from syftbox.services.syftbox_auth_service import SyftBoxAuthService
from users.utils import syftbox_jwt_expired


class SyftBoxTokenMixin:
    """
    Reusable SyftBox OAuth token helpers.

    Models using this mixin must expose:
    - ``syftbox_access_token`` field
    - ``syftbox_refresh_token`` field
    """

    @property
    def access_token(self) -> str:
        access = (self.syftbox_access_token or "").strip()
        refresh = (self.syftbox_refresh_token or "").strip()

        if not access and not refresh:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_CREDENTIALS,
                "SyftBox is not linked for this identity.",
                details={"id": self.pk},
            )

        if access and not syftbox_jwt_expired(access):
            return access

        if not refresh:
            raise SyftBoxException(
                SyftBoxErrorCode.TOKEN_EXPIRED,
                "SyftBox access token expired and no refresh token stored.",
                details={"id": self.pk},
            )

        tokens = SyftBoxAuthService().refresh_token(refresh)
        self.syftbox_access_token = tokens.access_token
        self.syftbox_refresh_token = tokens.refresh_token
        self.save(update_fields=["syftbox_access_token", "syftbox_refresh_token"])
        return tokens.access_token
