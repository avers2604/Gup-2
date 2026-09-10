from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme, TokenRefreshSerializerExtension


class PolicyJWTScheme(SimpleJWTScheme):
    target_class = "apps.iam.security.PolicyJWTAuthentication"


class PolicyRefreshSchema(TokenRefreshSerializerExtension):
    target_class = "apps.iam.security.PolicyTokenRefreshSerializer"
