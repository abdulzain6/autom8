"""
TODO:
Note: try to keep dependencies on other internal packages to a minimum.
Note: at the time of writing, it's still too early to do optimizations on the database schema,
but we should keep an eye on it and be prepared for potential future optimizations.
for example,
1. should enum where possible, such as Plan, Visibility, etc
2. create index on embedding and other fields that are frequently used for filtering
3. materialized views for frequently queried data
4. limit string length for fields that have string type
5. Note we might need to set up index for embedding manually for customizing the similarity search algorithm
   (https://github.com/pgvector/pgvector)
"""

# TODO: ideally shouldn't need it in python 3.12 for forward reference?
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SqlEnum

# Note: need to use postgresqlr ARRAY in order to use overlap operator
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
)

from aci.common.db.custom_sql_types import (
    EncryptedSecurityCredentials,
    EncryptedSecurityScheme,
)
from aci.common.enums import (
    Protocol,
    SecurityScheme,
)

EMBEDDING_DIMENSION = 768
APP_DEFAULT_VERSION = "1.0.0"
# need app to be shorter because it's used as prefix for function name
APP_NAME_MAX_LENGTH = 100
MAX_STRING_LENGTH = 255
MAX_ENUM_LENGTH = 50


class Base(MappedAsDataclass, DeclarativeBase):
    pass


class SupabaseUser(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)  # UUID
    email = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class UserProfile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(
        ForeignKey("users.id"),
        primary_key=True,
        default_factory=lambda: str(uuid4()),
        init=False,
    )
    name: Mapped[Optional[str]] = mapped_column(String(100))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(255))


class Function(Base):
    """
    Function is a callable function that can be executed.
    Each function belongs to one App.
    """

    __tablename__ = "functions"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid4()), init=False
    )
    app_id: Mapped[str] = mapped_column(String, ForeignKey("apps.id"), nullable=False)
    # Note: the function name is unique across the platform and should have app information, e.g., "GITHUB_CLONE_REPO"
    # ideally this should just be <app name>_<function name> (uppercase)
    name: Mapped[str] = mapped_column(
        String(MAX_STRING_LENGTH), nullable=False, unique=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    protocol: Mapped[Protocol] = mapped_column(SqlEnum(Protocol), nullable=False)
    protocol_data: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=False
    )
    # empty dict for function that takes no args
    parameters: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=False
    )
    # TODO: should response schema be generic (data + execution success of not + optional error) or specific to the function
    response: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=False
    )
    # TODO: should we provide EMBEDDING_DIMENSION here? which makes it less flexible if we want to change the embedding dimention in the future
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        init=False,
    )

    # the App that this function belongs to
    app: Mapped[App] = relationship(
        "App", lazy="select", back_populates="functions", init=False
    )

    @property
    def app_name(self) -> str:
        return str(self.app.name)


class App(Base):
    """
    Represents an application available in the system.
    Each app can now have at most one configuration.
    """

    __tablename__ = "apps"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid4()), init=False
    )
    name: Mapped[str] = mapped_column(
        String(APP_NAME_MAX_LENGTH), nullable=False, unique=True
    )
    display_name: Mapped[str] = mapped_column(String(MAX_STRING_LENGTH), nullable=False)
    provider: Mapped[str] = mapped_column(String(MAX_STRING_LENGTH), nullable=False)
    version: Mapped[str] = mapped_column(String(MAX_STRING_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    logo: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    security_schemes: Mapped[Dict[SecurityScheme, dict]] = mapped_column(
        MutableDict.as_mutable(EncryptedSecurityScheme),
        nullable=False,
    )
    embedding: Mapped[List[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        init=False,
    )

    functions: Mapped[List[Function]] = relationship(
        "Function",
        lazy="select",
        cascade="all, delete-orphan",
        back_populates="app",
        init=False,
    )

    configuration: Mapped["AppConfiguration"] = relationship(
        "AppConfiguration",
        back_populates="app",
        cascade="all, delete-orphan",
        uselist=False,  # Enforces one-to-one
        lazy="select",
        init=False,
    )

    default_credentials: Mapped["DefaultAppCredential"] = relationship(
        "DefaultAppCredential",
        back_populates="app",
        cascade="all, delete-orphan",
        uselist=False,  # Enforces one-to-one
        lazy="select",
        init=False,
    )

    linked_accounts: Mapped[List["LinkedAccount"]] = relationship(
        "LinkedAccount",
        back_populates="app",
        cascade="all, delete-orphan",
        lazy="select",
        init=False,
    )

    @property
    def has_default_credentials(self) -> bool:
        """
        Checks if the app has a set of default credentials.
        Returns True if default credentials exist, False otherwise.
        """
        return self.default_credentials is not None

    @property
    def has_configuration(self) -> bool:
        return self.configuration is not None

    def has_linked_account(self, user_id: str) -> bool:
        """
        Checks if the app has a linked account for the given user.

        Args:
            user_id: The unique identifier for the user.

        Returns:
            True if a linked account exists for this app and user, False otherwise.
        """
        return any(
            linked_account.user_id == user_id for linked_account in self.linked_accounts
        )

    def get_linked_account(self, user_id: str):
        for linked_account in self.linked_accounts:
            if user_id == user_id:
                return linked_account.id
        return None


class AppConfiguration(Base):
    """
    Represents the configuration for a single App.
    The app_id is now unique to enforce a one-to-one relationship.
    """

    __tablename__ = "app_configurations"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid4()), init=False
    )

    # UPDATED: Added unique=True to enforce one-to-one from this side
    app_id: Mapped[str] = mapped_column(
        String, ForeignKey("apps.id"), nullable=False, unique=True
    )

    security_scheme: Mapped[SecurityScheme] = mapped_column(
        SqlEnum(SecurityScheme), nullable=False
    )
    security_scheme_overrides: Mapped[Dict[SecurityScheme, dict]] = mapped_column(
        MutableDict.as_mutable(EncryptedSecurityScheme),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    all_functions_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    enabled_functions: Mapped[List[str]] = mapped_column(
        ARRAY(String(MAX_STRING_LENGTH)), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        init=False,
    )

    app: Mapped[App] = relationship(
        "App", back_populates="configuration", lazy="select", init=False
    )

    @property
    def app_name(self) -> str:
        """Returns the name of the associated app."""
        return str(self.app.name)


class DefaultAppCredential(Base):
    """
    Stores the default security credentials for a single App.
    An App can have at most one set of default credentials.
    """

    __tablename__ = "default_app_credentials"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid4()), init=False
    )

    # The app_id is unique to enforce a one-to-one relationship.
    app_id: Mapped[str] = mapped_column(
        String, ForeignKey("apps.id"), nullable=False, unique=True
    )

    security_scheme: Mapped[SecurityScheme] = mapped_column(
        SqlEnum(SecurityScheme), nullable=False
    )

    credentials: Mapped[Dict] = mapped_column(
        MutableDict.as_mutable(EncryptedSecurityCredentials),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        init=False,
    )

    # Relationship to the App model
    app: Mapped["App"] = relationship(
        "App", back_populates="default_credentials", lazy="select", init=False
    )


class LinkedAccount(Base):
    """
    Linked account is a specific account under an app, owned by a user.
    """

    __tablename__ = "linked_accounts"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid4()), init=False
    )

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)

    app_id: Mapped[str] = mapped_column(String, ForeignKey("apps.id"), nullable=False)

    security_scheme: Mapped[SecurityScheme] = mapped_column(
        SqlEnum(SecurityScheme), nullable=False
    )

    security_credentials: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(EncryptedSecurityCredentials),
        nullable=False,
    )

    disabled_functions: Mapped[List[str]] = mapped_column(
        ARRAY(String(MAX_STRING_LENGTH)), nullable=False, default_factory=list
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False, init=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        init=False,
    )

    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=True, init=False
    )

    app: Mapped[App] = relationship("App", lazy="select", init=False)
    user: Mapped[SupabaseUser] = relationship("SupabaseUser", lazy="select", init=False)

    @property
    def app_name(self) -> str:
        return str(self.app.name)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "app_id",
            name="uc_user_app_linked_account",
        ),
    )

    secrets: Mapped[list["Secret"]] = relationship(
        "Secret", lazy="select", cascade="all, delete-orphan", init=False
    )


class Secret(Base):
    __tablename__ = "secrets"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid4()), init=False
    )
    linked_account_id: Mapped[str] = mapped_column(
        String, ForeignKey("linked_accounts.id"), nullable=False
    )

    key: Mapped[str] = mapped_column(String(MAX_STRING_LENGTH), nullable=False)
    value: Mapped[bytes] = mapped_column(BYTEA, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        init=False,
    )

    __table_args__ = (
        UniqueConstraint("linked_account_id", "key", name="uc_linked_account_key"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    filer_path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))



__all__ = [
    "App",
    "AppConfiguration",
    "Base",
    "Function",
    "LinkedAccount",
    "Secret",
    "TempFile",
]
