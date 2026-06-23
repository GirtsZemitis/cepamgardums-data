# Sales dashboard — runs on pure Python stdlib (no pip deps needed at runtime).
FROM python:3.12-slim

WORKDIR /app
COPY . /app

# Azure Container Apps: set ingress targetPort to this value.
ENV PORT=8000
EXPOSE 8000

# Optional env (set in Azure):
#   XYNET_ACCOUNT, XYNET_INNER (or XYNET_PASSWORD)  -> enables auto-login / Refresh
#   DASH_PASSWORD (and optional DASH_USER)          -> password-protects the dashboard
#   AUTO_REFRESH=1                                  -> pull fresh data on startup
CMD ["python", "app.py"]
