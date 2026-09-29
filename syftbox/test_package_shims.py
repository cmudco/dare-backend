"""
The SyftBox implementation now lives in the shared ``syftbox-connect`` package.

DARE's own ``syftbox.*`` and ``core.storage.*`` modules are re-export shims so
existing imports keep resolving. These tests prove the shims are shims: every
name DARE imports must be the *same object* the package defines, not a copy
that could drift.

If someone re-implements something locally instead of re-exporting it, the
identity assertion below fails immediately rather than at runtime in
production.
"""

from django.test import SimpleTestCase

# Names re-exported by each shim, paired with the package module behind it.
SHIMMED = [
    (
        "syftbox.errors",
        "syftbox_connect.errors",
        ["SyftBoxErrorCode", "SyftBoxException"],
    ),
    (
        "syftbox.dtos",
        "syftbox_connect.dtos",
        ["AuthTokens", "RemoteSyftBoxFile", "SyftBoxSyncResult"],
    ),
    (
        "syftbox.enums",
        "syftbox_connect.enums",
        ["PermissionIdentifier", "PermissionPreset", "SyftBoxSyncAction"],
    ),
    (
        "syftbox.utils",
        "syftbox_connect.utils",
        [
            "has_remote_etag_change",
            "is_syftbox_acl_file",
            "normalize_syftbox_path",
            "raise_syftbox_error",
            "resolve_sync_action",
            "wrap_as_syftbox_error",
        ],
    ),
    ("syftbox.mixins", "syftbox_connect.mixins", ["SyftBoxTokenMixin"]),
    (
        "syftbox.services.http_client",
        "syftbox_connect.services.http_client",
        ["HttpClient"],
    ),
    (
        "syftbox.services.permission_builder",
        "syftbox_connect.services.permission_builder",
        ["PermissionBuilder"],
    ),
    (
        "syftbox.services.syftbox_auth_service",
        "syftbox_connect.services.syftbox_auth_service",
        ["SyftBoxAuthService"],
    ),
    (
        "syftbox.services.syftbox_file_service",
        "syftbox_connect.services.syftbox_file_service",
        ["SyftBoxFileService"],
    ),
    (
        "syftbox.services.syftbox_permission_service",
        "syftbox_connect.services.syftbox_permission_service",
        ["SyftBoxPermissionService"],
    ),
    (
        "core.storage.backends",
        "syftbox_connect.storage.backends",
        ["SyftBoxStorage"],
    ),
    (
        "core.storage.constants",
        "syftbox_connect.storage.constants",
        ["StorageBackendChoice"],
    ),
    (
        "core.storage.storage_service",
        "syftbox_connect.storage.storage_service",
        ["get_storage_service", "get_file_storage", "get_storage_for_user"],
    ),
]

ENDPOINT_NAMES = [
    "BASE_URL",
    "APP_NAME",
    "REQUEST_OTP",
    "VERIFY_OTP",
    "REFRESH_TOKEN",
    "BLOB_UPLOAD",
    "BLOB_DOWNLOAD",
    "BLOB_DELETE",
    "DATASITE_VIEW",
    "REQUEST_TIMEOUT",
]


def load(dotted):
    module = __import__(dotted, fromlist=["_"])
    return module


class ShimIdentityTests(SimpleTestCase):
    def test_every_shimmed_name_is_the_packages_own_object(self):
        for dare_module, package_module, names in SHIMMED:
            shim, package = load(dare_module), load(package_module)
            for name in names:
                with self.subTest(module=dare_module, name=name):
                    self.assertIs(
                        getattr(shim, name),
                        getattr(package, name),
                        f"{dare_module}.{name} is not {package_module}.{name}",
                    )

    def test_the_endpoint_constants_match(self):
        shim = load("syftbox.constants")
        package = load("syftbox_connect.constants")
        for name in ENDPOINT_NAMES:
            with self.subTest(name=name):
                self.assertEqual(getattr(shim, name), getattr(package, name))


class SettingsContractTests(SimpleTestCase):
    """The package reads Django settings; DARE must supply what it expects."""

    def test_dare_configures_every_setting_the_package_needs(self):
        from django.conf import settings

        from syftbox_connect.constants import DEFAULTS

        configured = getattr(settings, "SYFTBOX", None)
        self.assertIsNotNone(configured, "settings.SYFTBOX is missing")
        for key in DEFAULTS:
            with self.subTest(key=key):
                self.assertIn(key, configured)

    def test_the_base_url_drives_the_endpoints(self):
        from django.conf import settings

        from syftbox_connect.constants import REQUEST_OTP

        self.assertTrue(REQUEST_OTP.startswith(settings.SYFTBOX["BASE_URL"]))
