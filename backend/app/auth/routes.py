from fastapi import APIRouter, Depends, HTTPException, status
from shared.db import session_scope
from shared.models import User
from shared.security import create_access_token, hash_password, verify_password

from .dependencies import get_current_user
from .schemas import ChangePasswordRequest, LoginRequest, LoginResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    with session_scope() as db:
        user = db.query(User).filter(User.email == payload.email).first()
        if user is None or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

        token = create_access_token(subject=user.id)
        return LoginResponse(access_token=token, must_change_password=user.must_change_password)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)


@router.post("/change-password", response_model=UserOut)
def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
) -> UserOut:
    with session_scope() as db:
        user = db.get(User, current_user.id)
        if user is None or not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")

        user.password_hash = hash_password(payload.new_password)
        user.must_change_password = False
        db.flush()
        db.refresh(user)
        result = UserOut.model_validate(user)

    return result
