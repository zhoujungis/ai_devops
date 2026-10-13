"""User manager for the email-as-identifier user model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth.models import BaseUserManager

if TYPE_CHECKING:
    from apps.accounts.models import User


class UserManager(BaseUserManager["User"]):
    """Creates users identified by email instead of a username."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any) -> User:
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra)

    def create_from_github(
        self, github_id: int, login: str, email: str, avatar_url: str = ""
    ) -> User:
        """Create a user whose identity is their GitHub account.

        GitHub is the only way in, so the account is deliberately passwordless:
        ``set_unusable_password`` (rather than an empty hash) makes every
        ``authenticate()`` path reject it, closing a second, unmanaged door.
        """
        user = self.model(
            email=self.normalize_email(email).lower(),
            display_name=login,
            avatar_url=avatar_url or "",
            github_id=github_id,
            github_login=login,
        )
        user.set_unusable_password()
        user.save(using=self._db)
        return user
