from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """
    Only users with ADMIN role.
    """

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == "ADMIN"
        )


class IsSecurityGuard(BasePermission):
    """
    Only Security Guards.
    """

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == "SECURITY_GUARD"
        )


class IsFaculty(BasePermission):
    """
    Only Faculty.
    """

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == "FACULTY"
        )


class IsViewer(BasePermission):
    """
    Only Viewers.
    """

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == "VIEWER"
        )


class IsAdminOrSecurity(BasePermission):
    """
    Admins and Security Guards.
    """

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in ["ADMIN", "SECURITY_GUARD"]
        )