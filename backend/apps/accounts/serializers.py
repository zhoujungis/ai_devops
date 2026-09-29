"""Serializers for authentication, tenancy and membership.

Tenant identifiers (``org``, ``project``) are never writable. They are taken from
the URL by the view, so a request body can never place a row in a tenant the
caller does not control.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import (
    Membership,
    Organization,
    Project,
    ProjectMembership,
    User,
)
from apps.accounts.roles import Role
from apps.accounts.services import issue_tokens


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "display_name", "avatar_url", "date_joined", "is_active")
        read_only_fields = ("id", "email", "date_joined", "is_active")


class RegisterSerializer(serializers.Serializer):
    """Self-service signup.

    Supplying ``organization_name`` also creates that organization with the new
    user as its admin. Omitting it leaves the user organization-less until
    somebody invites them, which is the normal path for colleagues.
    """

    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )
    display_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    organization_name = serializers.CharField(max_length=200, required=False, allow_blank=True)

    def validate_email(self, value: str) -> str:
        email = value.strip().lower()
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("A user with this email address already exists.")
        return email

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def create(self, validated_data: dict[str, Any]) -> User:
        organization_name: str = validated_data.get("organization_name", "")
        with transaction.atomic():
            user = User.objects.create_user(
                email=validated_data["email"],
                password=validated_data["password"],
                display_name=validated_data.get("display_name", ""),
            )
            if organization_name:
                org = Organization.objects.create(name=organization_name)
                Membership.objects.create(org=org, user=user, role=Role.ADMIN)
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True, style={"input_type": "password"}, trim_whitespace=False
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        user = authenticate(
            request=self.context.get("request"),
            email=attrs["email"].strip().lower(),
            password=attrs["password"],
        )
        # A disabled account also lands here: ModelBackend.user_can_authenticate()
        # rejects it, so this single message covers unknown email, wrong password
        # and disabled account alike. That is deliberate — the response must not
        # reveal which accounts exist.
        if user is None:
            raise serializers.ValidationError(
                {"detail": "Invalid email or password."}, code="authorization"
            )
        return {**issue_tokens(user), "user": user}


class LoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def create(self, validated_data: dict[str, Any]) -> dict[str, Any]:
        try:
            RefreshToken(validated_data["refresh"]).blacklist()
        except TokenError as exc:
            raise serializers.ValidationError(
                {"refresh": "This refresh token is invalid or has already expired."}
            ) from exc
        return validated_data


class OrganizationSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ("id", "name", "slug", "plan", "settings", "created_at", "role")
        read_only_fields = ("id", "slug", "created_at", "role")

    def get_role(self, obj: Organization) -> str | None:
        """The caller's role, so a client can hide actions it cannot perform."""
        request = self.context.get("request")
        if request is None:
            return None
        role = obj.role_for(request.user)
        return role.value if role is not None else None


class OrganizationMemberSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    user_email = serializers.EmailField(write_only=True, required=False)

    class Meta:
        model = Membership
        fields = ("id", "user", "user_email", "role", "created_at")
        read_only_fields = ("id", "created_at")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if self.instance is None and not attrs.get("user_email"):
            raise serializers.ValidationError({"user_email": "This field is required."})
        # The last-admin invariant is enforced in OrganizationMemberViewSet, inside the
        # same transaction as the write, so the row lock it takes actually holds.
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Membership:
        email: str = validated_data.pop("user_email").strip().lower()
        # ``org`` is injected by the view from the URL, never from the body.
        org: Organization = validated_data.pop("org")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist as exc:
            raise serializers.ValidationError(
                {"user_email": "No user with this email address. They must sign up first."}
            ) from exc

        if Membership.objects.filter(org=org, user=user).exists():
            raise serializers.ValidationError({"user_email": "This user is already a member."})

        return Membership.objects.create(org=org, user=user, **validated_data)


class ProjectSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    # Reading `org` (the relation) with a UUIDField would serialise the Organization
    # through `str()` and yield its display name; every project-scoped URL the client
    # builds from it would then 404. `source="org_id"` serialises the id itself.
    org = serializers.UUIDField(source="org_id", read_only=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "org",
            "name",
            "slug",
            "description",
            "key_prefix",
            "status",
            "settings",
            "created_at",
            "role",
        )
        read_only_fields = ("id", "org", "slug", "created_at", "role")

    def get_role(self, obj: Project) -> str | None:
        request = self.context.get("request")
        if request is None:
            return None
        role = obj.role_for(request.user)
        return role.value if role is not None else None


class ProjectMemberSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    user_email = serializers.EmailField(write_only=True, required=False)

    class Meta:
        model = ProjectMembership
        fields = ("id", "user", "user_email", "role", "created_at")
        read_only_fields = ("id", "created_at")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if self.instance is None and not attrs.get("user_email"):
            raise serializers.ValidationError({"user_email": "This field is required."})
        return attrs

    def create(self, validated_data: dict[str, Any]) -> ProjectMembership:
        email: str = validated_data.pop("user_email").strip().lower()
        # ``project`` is injected by the view from the URL, never from the body.
        project: Project = validated_data.pop("project")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist as exc:
            raise serializers.ValidationError(
                {"user_email": "No user with this email address. They must sign up first."}
            ) from exc

        if ProjectMembership.objects.filter(project=project, user=user).exists():
            raise serializers.ValidationError(
                {"user_email": "This user already has a role on this project."}
            )

        return ProjectMembership.objects.create(project=project, user=user, **validated_data)
