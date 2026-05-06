from config import env

BASE_URL = env.SYFTBOX_BASE_URL
APP_NAME = env.SYFTBOX_APP_NAME

REQUEST_OTP = f"{BASE_URL}/auth/otp/request"
VERIFY_OTP = f"{BASE_URL}/auth/otp/verify"
REFRESH_TOKEN = f"{BASE_URL}/auth/refresh"
BLOB_UPLOAD = f"{BASE_URL}/api/v1/blob/upload"
# syft.pub.yaml: create/update via upload/acl (not generic /blob/upload).
BLOB_UPLOAD_ACL = f"{BASE_URL}/api/v1/blob/upload/acl"
BLOB_DOWNLOAD = f"{BASE_URL}/api/v1/blob/download"
BLOB_DELETE = f"{BASE_URL}/api/v1/blob/delete"
DATASITE_VIEW = f"{BASE_URL}/api/v1/datasite/view"
DATASITE_SYNC_FOLDER = f"app_data/{APP_NAME}/files/"

REQUEST_TIMEOUT = 15
