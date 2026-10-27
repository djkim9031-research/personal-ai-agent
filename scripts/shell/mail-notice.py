#!/usr/bin/python3 -I
"""Emails the owner when the agent starts or stops reading mail.

  mail-notice.py online    after the email adapter has taken its baseline
  mail-notice.py offline   after the gateway stopped, if online was sent

Runs as hermes from agent-hermes.service. Reads the mailbox settings from
$HERMES_HOME/.env and never prints the password. At most 3 online notices an
hour, so a restart loop cannot flood the owner's inbox.
"""
import os
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
STATE = os.path.join(RUN, "agent-hermes.notices")
LOG = os.path.join(HOME, "logs", "agent.log")
READY = re.compile(r"\[Email\] IMAP connection test passed\. (\d+) existing messages skipped")


def settings():
    env = {}
    with open(os.path.join(HOME, ".env")) as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            if sep and key.startswith("EMAIL_"):
                env[key] = value.strip().strip("'\"")
    return env


def history():
    try:
        with open(STATE) as f:
            return [line.split() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def remember(kind):
    with open(STATE, "a") as f:
        f.write(f"{kind} {int(time.time())}\n")


def wait_for_baseline(seconds=150):
    try:
        start = int(open(os.path.join(RUN, "agent-hermes.logpos")).read().strip() or 0)
    except (FileNotFoundError, ValueError):
        start = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with open(LOG, errors="replace") as f:
                f.seek(start)
                m = READY.search(f.read())
            if m:
                return int(m.group(1))
        except FileNotFoundError:
            pass
        time.sleep(2)
    return None


def send(env, subject, body):
    owner = env["EMAIL_ALLOWED_USERS"].split(",")[0].strip()
    msg = EmailMessage()
    msg["From"] = env["EMAIL_ADDRESS"]
    msg["To"] = owner
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=env["EMAIL_ADDRESS"].split("@")[-1])
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(body)
    port = int(env.get("EMAIL_SMTP_PORT", "465"))
    ctx = ssl.create_default_context()
    if port == 465:
        smtp = smtplib.SMTP_SSL(env["EMAIL_SMTP_HOST"], port, context=ctx, timeout=20)
    else:
        smtp = smtplib.SMTP(env["EMAIL_SMTP_HOST"], port, timeout=20)
        smtp.starttls(context=ctx)
    with smtp:
        smtp.login(env["EMAIL_ADDRESS"], env["EMAIL_PASSWORD"])
        smtp.send_message(msg)


def main():
    kind = sys.argv[1] if len(sys.argv) == 2 else ""
    if kind not in ("online", "offline"):
        sys.exit(__doc__)
    env = settings()
    past = history()
    now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    if kind == "online":
        if sum(1 for k, t in past if k == "online" and time.time() - int(t) < 3600) >= 3:
            print("online notice skipped: 3 already this hour")
            return
        skipped = wait_for_baseline()
        if skipped is None:
            subject = "Agent started, mailbox not confirmed"
            body = (f"The agent started at {now}, but its mailbox connection was not confirmed "
                    "within 150 seconds. Check: journalctl -u agent-hermes")
        else:
            subject = "Agent online"
            body = (f"The agent reads mail from {now}. {skipped} messages already in its mailbox "
                    "were skipped; send again anything you wrote while it was offline.")
    else:
        if not past or past[-1][0] != "online":
            print("offline notice skipped: no online notice for this run")
            return
        result = os.environ.get("SERVICE_RESULT", "success")
        subject = "Agent offline"
        body = (f"The agent stopped reading mail at {now}. Mail sent from now on is not answered; "
                "it starts again with the model server.")
        if result != "success":
            body += f" (gateway result: {result})"
    try:
        send(env, subject, body)
    except (OSError, smtplib.SMTPException, KeyError) as e:
        sys.exit(f"{kind} notice not sent: {type(e).__name__}")
    remember(kind)
    print(f"{kind} notice sent")


if __name__ == "__main__":
    main()
