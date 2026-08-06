from datetime import date, datetime

from pydantic import BaseModel, Field


class AdminDashboardSummary(BaseModel):
    total_users: int
    new_users_today: int
    new_users_this_month: int
    total_api_keys: int
    active_api_keys: int
    total_requests_today: int
    total_requests_this_month: int
    active_subscriptions: int | None
    subscriptions_connected: bool = False


class AdminUserListItem(BaseModel):
    id: str
    display_name: str
    email: str
    organization: str | None
    created_at: datetime
    plan: str
    api_key_status: str
    is_admin: bool
    active: bool


class AdminUserList(BaseModel):
    items: list[AdminUserListItem]
    total: int
    limit: int
    offset: int


class AdminApiKey(BaseModel):
    id: str
    name: str | None
    key_prefix: str
    active: bool
    created_at: datetime
    last_used_at: datetime | None


class AdminSubscription(BaseModel):
    plan: str
    status: str
    provider: str | None
    current_period_end: datetime | None


class AdminActivity(BaseModel):
    kind: str
    label: str
    occurred_at: datetime


class AdminUserDetails(BaseModel):
    id: str
    display_name: str
    email: str
    organization: str | None
    created_at: datetime
    last_login_at: datetime | None
    active: bool
    is_admin: bool
    plan: str
    api_keys: list[AdminApiKey]
    request_count_today: int
    request_count_month: int
    lookup_count_month: int
    usage_period: date
    subscription: AdminSubscription | None
    recent_activity: list[AdminActivity]


class AdminAccountStatusUpdate(BaseModel):
    active: bool


class AdminRoleUpdate(BaseModel):
    is_admin: bool


class AdminRegenerateKeyRequest(BaseModel):
    key_id: str | None = None
    name: str = Field(default="admin-regenerated", min_length=1, max_length=128)


class AdminRegeneratedKey(BaseModel):
    id: str
    key_prefix: str
    api_key: str
    created_at: datetime
