from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def send_verification_email(*, to_email: str, recipient_name: str, verification_url: str) -> None:
    subject = "Verify your TryClothes account"
    text_body = (
        f"Hi {recipient_name},\n\n"
        "Welcome to TryClothes.\n"
        "Please verify your email address by opening the link below:\n\n"
        f"{verification_url}\n\n"
        "If you did not create this account, you can ignore this message."
    )

    _deliver_email(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=_verification_html(recipient_name, verification_url),
        log_label="Verification email",
        log_target=verification_url,
    )


def send_password_reset_email(*, to_email: str, recipient_name: str, reset_url: str) -> None:
    subject = "Reset your TryClothes password"
    text_body = (
        f"Hi {recipient_name},\n\n"
        "We received a request to reset your TryClothes password.\n"
        "Open the link below to choose a new password:\n\n"
        f"{reset_url}\n\n"
        "If you did not request this change, you can ignore this message."
    )

    _deliver_email(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=_password_reset_email_html(recipient_name, reset_url),
        log_label="Password reset email",
        log_target=reset_url,
    )


def _deliver_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str,
    log_label: str,
    log_target: str,
) -> None:
    if settings.email_delivery_mode_normalized == "smtp":
        _send_via_smtp(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        return

    if settings.email_delivery_mode_normalized == "resend":
        _send_via_resend(
            to_email=to_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        return

    logger.info("%s for %s -> %s", log_label, to_email, log_target)


def _send_via_smtp(*, to_email: str, subject: str, text_body: str, html_body: str) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        raise RuntimeError("SMTP is not fully configured.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _formatted_from_address()
    message["To"] = to_email
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls()
        smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(message)


def _send_via_resend(*, to_email: str, subject: str, text_body: str, html_body: str) -> None:
    if not settings.RESEND_API_KEY.strip():
        raise RuntimeError("Resend API key is not configured.")
    if not settings.EMAIL_FROM.strip():
        raise RuntimeError("EMAIL_FROM is not configured.")

    base_url = settings.RESEND_API_BASE_URL.rstrip("/")
    payload = {
        "from": _formatted_from_address(),
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30.0) as client:
        response = client.post(f"{base_url}/emails", json=payload, headers=headers)

    if response.status_code >= 400:
        detail = _extract_resend_error(response)
        raise RuntimeError(f"Resend email delivery failed: {detail}")


def _formatted_from_address() -> str:
    sender = settings.EMAIL_FROM.strip()
    if "<" in sender and ">" in sender:
        return sender
    sender_name = settings.EMAIL_FROM_NAME.strip()
    if sender_name:
        return f"{sender_name} <{sender}>"
    return sender


def _extract_resend_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if message:
            return str(message)
    return response.text or f"HTTP {response.status_code}"


def verification_result_html(*, title: str, message: str, success: bool) -> str:
    accent = "#1f8f63" if success else "#b94b52"
    accent_soft = "rgba(31, 143, 99, 0.14)" if success else "rgba(185, 75, 82, 0.14)"
    icon = "✓" if success else "!"
    status_label = "Account Ready" if success else "Action Needed"
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background:
        radial-gradient(circle at top left, rgba(197,160,89,0.18), transparent 34%),
        radial-gradient(circle at top right, rgba(32,26,22,0.08), transparent 28%),
        linear-gradient(145deg, #f6f0e8 0%, #fbf8f4 48%, #ffffff 100%);
      color: #1f1a16;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 28px;
    }}
    .shell {{
      width: min(560px, 100%);
      position: relative;
    }}
    .glow {{
      position: absolute;
      inset: 18px;
      border-radius: 36px;
      background: linear-gradient(135deg, rgba(197,160,89,0.18), rgba(255,255,255,0.02));
      filter: blur(24px);
      z-index: 0;
    }}
    .card {{
      position: relative;
      z-index: 1;
      overflow: hidden;
      background: rgba(255,255,255,0.82);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
      border-radius: 34px;
      padding: 34px 32px 28px;
      box-shadow:
        0 28px 80px rgba(33, 24, 18, 0.12),
        inset 0 1px 0 rgba(255,255,255,0.72);
      border: 1px solid rgba(92, 73, 52, 0.10);
    }}
    .card::before {{
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.72), rgba(255,255,255,0.24)),
        radial-gradient(circle at top right, rgba(197,160,89,0.14), transparent 32%);
      pointer-events: none;
    }}
    .eyebrow {{
      color: #81642d;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      margin-bottom: 18px;
      opacity: 0.92;
    }}
    .topline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 22px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: {accent_soft};
      color: {accent};
      border: 1px solid rgba(0,0,0,0.05);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .icon-wrap {{
      width: 74px;
      height: 74px;
      border-radius: 24px;
      display: grid;
      place-items: center;
      background:
        linear-gradient(145deg, rgba(255,255,255,0.96), rgba(247,240,232,0.76));
      border: 1px solid rgba(0,0,0,0.06);
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.94),
        0 14px 30px rgba(32,26,22,0.10);
    }}
    .icon-core {{
      width: 46px;
      height: 46px;
      border-radius: 16px;
      display: grid;
      place-items: center;
      background: {accent_soft};
      color: {accent};
      font-size: 26px;
      font-weight: 800;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 34px;
      line-height: 1.02;
      letter-spacing: -0.03em;
    }}
    p {{
      margin: 0;
      color: #5f554b;
      line-height: 1.68;
      font-size: 15px;
      max-width: 44ch;
    }}
    .divider {{
      height: 1px;
      margin: 22px 0 18px;
      background: linear-gradient(90deg, rgba(129,100,45,0), rgba(129,100,45,0.22), rgba(129,100,45,0));
    }}
    .footer {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      font-size: 13px;
      color: #857869;
    }}
    .chip {{
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(32,26,22,0.04);
      border: 1px solid rgba(32,26,22,0.06);
    }}
    @media (max-width: 560px) {{
      body {{
        padding: 18px;
      }}
      .card {{
        padding: 26px 22px 22px;
        border-radius: 28px;
      }}
      .topline {{
        align-items: flex-start;
      }}
      h1 {{
        font-size: 30px;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="glow"></div>
    <div class="card">
      <div class="eyebrow">TryClothes</div>
      <div class="topline">
        <div>
          <div class="badge">{status_label}</div>
        </div>
        <div class="icon-wrap">
          <div class="icon-core">{icon}</div>
        </div>
      </div>
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(message)}</p>
      <div class="divider"></div>
      <div class="footer">
        <div class="chip">Secure account flow</div>
        <div>Return to the app when you're ready.</div>
      </div>
    </div>
  </div>
</body>
</html>
""".strip()


def password_reset_form_html(*, token: str, error_message: str | None = None, password_updated: bool = False) -> str:
    escaped_token = html.escape(token)
    error_block = ""
    if error_message:
        error_block = (
            '<div class="notice notice-error">'
            f"{html.escape(error_message)}"
            "</div>"
        )

    if password_updated:
        return verification_result_html(
            title="Password Updated",
            message="Your password was updated successfully. You can return to the app and sign in with the new password.",
            success=True,
        )

    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Reset your password</title>
  <style>
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: linear-gradient(135deg, #f5efe7, #ffffff);
      color: #201a16;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
    }}
    .card {{
      width: min(560px, 100%);
      background: rgba(255,255,255,0.95);
      border-radius: 28px;
      padding: 32px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.10);
      border: 1px solid rgba(0,0,0,0.06);
    }}
    .eyebrow {{
      color: #775a19;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      margin-bottom: 14px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 30px;
      line-height: 1.05;
    }}
    p {{
      margin: 0 0 20px;
      color: #5d5145;
      line-height: 1.6;
      font-size: 15px;
    }}
    label {{
      display: block;
      margin: 0 0 10px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: #7c6e60;
    }}
    input {{
      width: 100%;
      box-sizing: border-box;
      border-radius: 18px;
      border: 1px solid rgba(0,0,0,0.10);
      background: #f8f3ed;
      padding: 16px 18px;
      margin-bottom: 18px;
      font-size: 15px;
      color: #201a16;
    }}
    input:focus {{
      outline: none;
      border-color: rgba(119,90,25,0.55);
      box-shadow: 0 0 0 4px rgba(197,160,89,0.14);
    }}
    button {{
      appearance: none;
      border: none;
      border-radius: 20px;
      padding: 16px 20px;
      width: 100%;
      cursor: pointer;
      color: white;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      background: linear-gradient(135deg, #775a19, #c5a059);
      box-shadow: 0 16px 34px rgba(119,90,25,0.24);
    }}
    .notice {{
      border-radius: 18px;
      padding: 14px 16px;
      margin-bottom: 18px;
      font-size: 14px;
      line-height: 1.5;
    }}
    .notice-error {{
      background: rgba(156,47,47,0.10);
      color: #8f3434;
      border: 1px solid rgba(156,47,47,0.12);
    }}
    .meta {{
      margin-top: 18px;
      color: #7c6e60;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="eyebrow">TryClothes</div>
    <h1>Reset your password</h1>
    <p>Choose a new password for your account, then return to the app and sign in again.</p>
    {error_block}
    <form method="post" action="{settings.API_V1_PREFIX}/auth/reset-password/form">
      <input type="hidden" name="token" value="{escaped_token}" />
      <label for="password">New Password</label>
      <input id="password" name="password" type="password" minlength="8" maxlength="128" placeholder="Minimum 8 characters" required />
      <label for="confirm_password">Confirm Password</label>
      <input id="confirm_password" name="confirm_password" type="password" minlength="8" maxlength="128" placeholder="Repeat password" required />
      <button type="submit">Update Password</button>
    </form>
    <div class="meta">If you did not request this change, you can close this page.</div>
  </div>
</body>
</html>
""".strip()


def _verification_html(recipient_name: str, verification_url: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Verify your TryClothes account</title>
</head>
<body style="margin:0;padding:32px;background:#f5efe7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#201a16;">
  <div style="max-width:520px;margin:0 auto;background:rgba(255,255,255,0.96);border-radius:28px;padding:32px;border:1px solid rgba(0,0,0,0.06);box-shadow:0 20px 60px rgba(0,0,0,0.10);">
    <div style="font-size:12px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#775a19;margin-bottom:14px;">TryClothes</div>
    <h1 style="margin:0 0 14px;font-size:28px;line-height:1.1;">Verify your email</h1>
    <p style="margin:0 0 18px;line-height:1.6;color:#5d5145;">Hi {html.escape(recipient_name)}, confirm your email address to activate your account and start saving try-ons and fit data.</p>
    <a href="{html.escape(verification_url)}" style="display:inline-block;padding:14px 20px;border-radius:18px;background:linear-gradient(135deg,#775a19,#c5a059);color:#fff;text-decoration:none;font-weight:700;">Verify Email</a>
    <p style="margin:18px 0 0;line-height:1.6;color:#5d5145;">If you did not create this account, you can safely ignore this email.</p>
  </div>
</body>
</html>
""".strip()


def _password_reset_email_html(recipient_name: str, reset_url: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Reset your TryClothes password</title>
</head>
<body style="margin:0;padding:32px;background:#f5efe7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#201a16;">
  <div style="max-width:520px;margin:0 auto;background:rgba(255,255,255,0.96);border-radius:28px;padding:32px;border:1px solid rgba(0,0,0,0.06);box-shadow:0 20px 60px rgba(0,0,0,0.10);">
    <div style="font-size:12px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:#775a19;margin-bottom:14px;">TryClothes</div>
    <h1 style="margin:0 0 14px;font-size:28px;line-height:1.1;">Reset your password</h1>
    <p style="margin:0 0 18px;line-height:1.6;color:#5d5145;">Hi {html.escape(recipient_name)}, open the secure link below to choose a new password for your account.</p>
    <a href="{html.escape(reset_url)}" style="display:inline-block;padding:14px 20px;border-radius:18px;background:linear-gradient(135deg,#775a19,#c5a059);color:#fff;text-decoration:none;font-weight:700;">Reset Password</a>
    <p style="margin:18px 0 0;line-height:1.6;color:#5d5145;">If you did not request a password reset, you can safely ignore this email.</p>
  </div>
</body>
</html>
""".strip()
