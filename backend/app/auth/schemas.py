import re

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email_format(value: str) -> str:
    if not _EMAIL_RE.match(value):
        raise ValueError("value is not a valid email address")
    return value


class LoginRequest(BaseModel):
    email: str
    password: str

    _check_email = field_validator("email")(_validate_email_format)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: str
    email: str
    must_change_password: bool

    model_config = {"from_attributes": True}
