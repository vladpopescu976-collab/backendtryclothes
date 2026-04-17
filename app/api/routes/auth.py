from datetime import timedelta
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.schemas.common import MessageResponse
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    ResendVerificationRequest,
    SocialLoginRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.services.auth import (
    authenticate_user,
    create_guest_session_user,
    create_user,
    get_or_create_social_user,
    get_user_by_email,
    request_password_reset,
    resend_verification_email,
    reset_password_with_token,
    send_verification_for_user,
    validate_password_reset_token,
    verify_email_token,
)
from app.services.email import password_reset_form_html, verification_result_html

router = APIRouter()
logger = logging.getLogger(__name__)


def ensure_email_delivery_ready() -> None:
    if settings.APP_ENV.strip().lower() != "production":
        return
    if settings.email_can_deliver_to_real_inboxes:
        return
    raise HTTPException(
        status_code=503,
        detail="Email delivery is not configured on the server yet. Configure SMTP before creating real accounts.",
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> RegisterResponse:
    ensure_email_delivery_ready()
    existing_user = get_user_by_email(db, payload.email)
    if existing_user:
        raise HTTPException(status_code=409, detail="Email is already registered.")

    user = create_user(db, payload.first_name, payload.last_name, payload.email, payload.password)
    try:
        send_verification_for_user(db, user)
    except Exception as exc:
        logger.exception("Registration verification email failed for %s", payload.email)
        db.delete(user)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="We couldn't send the verification email right now. Please try again after email delivery is configured.",
        )
    return RegisterResponse(
        message="Account created. Please check your email and verify your account before signing in.",
        email=user.email,
        requires_verification=True,
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Please verify your email before signing in.")

    token = create_access_token(user.id, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return TokenResponse(access_token=token, user=user)


@router.post("/social", response_model=TokenResponse)
def social_login(payload: SocialLoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = get_or_create_social_user(
        db,
        provider=payload.provider,
        provider_subject=payload.provider_subject,
        email=payload.email,
        display_name=payload.display_name,
    )
    token = create_access_token(user.id, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return TokenResponse(access_token=token, user=user)


@router.post("/guest", response_model=TokenResponse)
def guest_login(db: Session = Depends(get_db)) -> TokenResponse:
    user = create_guest_session_user(db)
    token = create_access_token(user.id, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return TokenResponse(access_token=token, user=user)


@router.post("/verify-email", response_model=TokenResponse)
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = verify_email_token(db, payload.token)
    token = create_access_token(user.id, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return TokenResponse(access_token=token, user=user)


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email_from_link(token: str = Query(..., min_length=16), db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        verify_email_token(db, token)
    except HTTPException as exc:
        title = "Verification Failed"
        message = exc.detail if isinstance(exc.detail, str) else "The verification link is not valid anymore."
        return HTMLResponse(
            content=verification_result_html(title=title, message=message, success=False),
            status_code=exc.status_code,
        )

    return HTMLResponse(
        content=verification_result_html(
            title="Email Verified",
            message="Your account is now active. You can return to the app and sign in.",
            success=True,
        ),
        status_code=200,
    )


@router.post("/resend-verification")
def resend_verification(payload: ResendVerificationRequest, db: Session = Depends(get_db)) -> RegisterResponse:
    ensure_email_delivery_ready()
    try:
        did_send = resend_verification_email(db, payload.email)
    except Exception:
        logger.exception("Resend verification email failed for %s", payload.email)
        raise HTTPException(
            status_code=503,
            detail="We couldn't send the verification email right now. Please try again in a few moments.",
        )
    return RegisterResponse(
        message=(
            "If this email belongs to an unverified account, a new verification email has been sent."
            if did_send
            else "If this email belongs to an unverified account, a new verification email has been sent."
        ),
        email=payload.email,
        requires_verification=True,
    )


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)) -> MessageResponse:
    ensure_email_delivery_ready()
    try:
        request_password_reset(db, payload.email)
    except Exception:
        logger.exception("Password reset email failed for %s", payload.email)
        raise HTTPException(
            status_code=503,
            detail="We couldn't send the reset email right now. Please try again in a few moments.",
        )
    return MessageResponse(
        message="If this email belongs to an account, a password reset email has been sent."
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)) -> MessageResponse:
    reset_password_with_token(db, payload.token, payload.password)
    return MessageResponse(
        message="Your password was updated. You can sign in with the new password now."
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_form(token: str = Query(..., min_length=16), db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        validate_password_reset_token(db, token)
    except HTTPException as exc:
        title = "Reset Link Failed"
        message = exc.detail if isinstance(exc.detail, str) else "The reset link is not valid anymore."
        return HTMLResponse(
            content=verification_result_html(title=title, message=message, success=False),
            status_code=exc.status_code,
        )

    return HTMLResponse(content=password_reset_form_html(token=token), status_code=200)


@router.post("/reset-password/form", response_class=HTMLResponse)
def reset_password_form_submit(
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        payload = ResetPasswordRequest(token=token, password=password, confirm_password=confirm_password)
        reset_password_with_token(db, payload.token, payload.password)
    except HTTPException as exc:
        message = exc.detail if isinstance(exc.detail, str) else "The reset link is not valid anymore."
        return HTMLResponse(
            content=password_reset_form_html(token=token, error_message=message),
            status_code=exc.status_code,
        )
    except ValidationError as exc:
        return HTMLResponse(
            content=password_reset_form_html(token=token, error_message=exc.errors()[0]["msg"]),
            status_code=400,
        )
    except Exception as exc:  # pragma: no cover - defensive HTML fallback
        detail = str(exc) if str(exc) else "The password could not be updated."
        return HTMLResponse(
            content=password_reset_form_html(token=token, error_message=detail),
            status_code=400,
        )

    return HTMLResponse(content=password_reset_form_html(token=token, password_updated=True), status_code=200)
