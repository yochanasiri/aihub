import asyncio
import smtplib
import ssl
from email.message import EmailMessage


class SMTPMailer:
    def __init__(self, config): self.config = config

    async def send_code(self, email, code):
        c = self.config
        if not c.smtp_host:
            raise RuntimeError("SMTP is not configured")
        def send():
            message = EmailMessage()
            message["From"],message["To"] = c.smtp_from,email
            message["Subject"] = "Verify your IIT Indore AIHUB account"
            message.set_content(f"Your AIHUB verification code is: {code}\n\n"
                "It expires in 15 minutes. Enter it with the password you chose during registration.\n"
                "If you did not request this account, ignore this email. Do not share the code.")
            context = ssl.create_default_context()
            cls = smtplib.SMTP_SSL if c.smtp_ssl else smtplib.SMTP
            kwargs = {"context":context} if c.smtp_ssl else {}
            with cls(c.smtp_host,c.smtp_port,timeout=15,**kwargs) as smtp:
                if c.smtp_starttls:
                    smtp.starttls(context=context)
                if c.smtp_username:
                    smtp.login(c.smtp_username,c.smtp_password)
                smtp.send_message(message)
        await asyncio.to_thread(send)
