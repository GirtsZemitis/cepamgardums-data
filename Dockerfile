# Sales dashboard — runs on pure Python stdlib (no pip deps needed at runtime).
FROM python:3.12-slim

WORKDIR /app
# IANA tz database so ZoneInfo("Europe/Riga") works (Debian slim lacks it)
RUN pip install --no-cache-dir tzdata
COPY . /app

# Listen on 80 (Azure Container Apps convention). app.py honors $PORT, so if the
# platform injects a different PORT it follows that too. Set ingress targetPort = 80.
ENV PORT=80
EXPOSE 80

# Optional env (set in Azure):
#   XYNET_ACCOUNT, XYNET_INNER (or XYNET_PASSWORD)  -> enables auto-login / Refresh
#   DASH_PASSWORD (and optional DASH_USER)          -> password-protects the dashboard
#   AUTO_REFRESH=1                                  -> pull fresh data on startup
CMD ["python", "app.py"]
