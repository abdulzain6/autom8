from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SqlEnum

# Note: need to use postgresqlr ARRAY in order to use overlap operator
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, TSVECTOR
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
    RunStatus,
    SecurityScheme,
)

EMBEDDING_DIMENSION = 1536
APP_DEFAULT_VERSION = "1.0.0"
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
    )
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    def __init__(self, id: str, **kwargs):
        self.id = id
        super().__init__(**kwargs)


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

    parameters: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=False
    )
    response: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=False
    )
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default_factory=lambda: datetime.now(timezone.utc), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default_factory=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
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


automation_template_apps = Table(
    "automation_template_apps",
    Base.metadata,
    Column(
        "template_id", String, ForeignKey("automation_templates.id"), primary_key=True
    ),
    Column("app_id", String, ForeignKey("apps.id"), primary_key=True),
)


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
        DateTime(timezone=False), default_factory=lambda: datetime.now(timezone.utc), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default_factory=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
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
        DateTime(timezone=False), default_factory=lambda: datetime.now(timezone.utc), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default_factory=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
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
        DateTime(timezone=False), default_factory=lambda: datetime.now(timezone.utc), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default_factory=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
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
        DateTime(timezone=False), default_factory=lambda: datetime.now(timezone.utc), nullable=False, init=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default_factory= lambda: datetime.now(timezone.utc),
        onupdate= lambda: datetime.now(timezone.utc),
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
        DateTime(timezone=False), default_factory=lambda: datetime.now(timezone.utc), nullable=False, init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default_factory=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
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
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid.uuid4()), init=False
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    user: Mapped[SupabaseUser] = relationship("SupabaseUser", lazy="select", init=False)
    run_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("automation_runs.id"))


class Automation(Base):
    __tablename__ = "automations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), init=False)
    cron_schedule: Mapped[Optional[str]] = mapped_column(String(255))
    linked_accounts: Mapped[List["AutomationLinkedAccount"]] = relationship(
        back_populates="automation", cascade="all, delete-orphan", init=False
    )
    runs: Mapped[List["AutomationRun"]] = relationship(
        back_populates="automation", cascade="all, delete-orphan", init=False
    )
    goal: Mapped[str] = mapped_column(Text)
    is_deep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_recurring: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), default=RunStatus.never_run
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid.uuid4()), init=False
    )


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    automation_id: Mapped[str] = mapped_column(
        ForeignKey("automations.id"), nullable=False
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), init=False
    )

    logs: Mapped[Optional[dict]] = mapped_column(JSON, init=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    automation: Mapped["Automation"] = relationship(back_populates="runs", init=False)
    artifacts: Mapped[List["Artifact"]] = relationship(
        "Artifact",
        secondary="automation_run_artifacts",
        back_populates="automation_runs",
        init=False,
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), default=RunStatus.in_progress
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default_factory=lambda: datetime.now(timezone.utc)
    )
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid.uuid4()), init=False
    )
    artifacts: Mapped[List["Artifact"]] = relationship(
        "Artifact",
        # This tells SQLAlchemy to delete all child artifacts when this run is deleted.
        cascade="all, delete-orphan",
        # This back-populates the run attribute on the Artifact model if you add it.
        # back_populates="run", 
        init=False,
    )


class AutomationLinkedAccount(Base):
    __tablename__ = "automation_linked_accounts"

    automation_id: Mapped[str] = mapped_column(
        ForeignKey("automations.id", ondelete="CASCADE"), nullable=False
    )
    linked_account_id: Mapped[str] = mapped_column(
        ForeignKey("linked_accounts.id", ondelete="CASCADE"), nullable=False
    )
    automation: Mapped["Automation"] = relationship(
        "Automation", back_populates="linked_accounts", init=False
    )
    linked_account: Mapped["LinkedAccount"] = relationship(
        "LinkedAccount", lazy="select", init=False
    )
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid.uuid4()), init=False
    )


class AutomationTemplate(Base):
    """
    Represents a pre-built automation template that users can instantiate.
    """

    __tablename__ = "automation_templates"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(
        Text, nullable=False, comment="A Jinja2 template for the automation's goal."
    )
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid.uuid4())
    )
    search_vector: Mapped[TSVECTOR] = mapped_column(
        TSVECTOR,
        nullable=True,
        init=False,
        # This tells SQLAlchemy to expect the database to generate this value.
        server_default=None, 
    )
    variable_names: Mapped[List[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default_factory=list,
        comment="List of variable names used in the goal template.",
    )
    is_deep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tags: Mapped[List[str]] = mapped_column(
        ARRAY(String), nullable=False, default_factory=list
    )
    # Many-to-many relationship with App
    required_apps: Mapped[List["App"]] = relationship(
        "App",
        secondary=automation_template_apps,
        lazy="selectin",  # Use 'selectin' for efficient loading of related apps
        init=False,
    )

    __table_args__ = (
        Index(
            'ix_automation_templates_search_vector',
            'search_vector',
            postgresql_using='gin'
        ),
    )


__all__ = [
    "App",
    "AppConfiguration",
    "Base",
    "Function",
    "LinkedAccount",
    "Secret",
    "Artifact",
    "Automation",
    "AutomationRun",
    "AutomationLinkedAccount",
]
