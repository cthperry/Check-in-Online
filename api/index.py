"""Vercel �� WSGI �J�f�C"""
import os
from functools import lru_cache

from attendance.web import application as make_attendance_app


@lru_cache(maxsize=8)
def get_app(origin):
    return make_attendance_app(os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL"), origin)


def app(environ, start_response):
    hosts = {os.environ.get(key) for key in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_BRANCH_URL")}
    host = environ.get("HTTP_HOST")
    origin = os.environ.get("ATTENDANCE_ORIGIN")
    if host and host in hosts:
        origin = "https://" + host
    if not origin:
        origin = "https://" + (os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or "checkinonline.vercel.app")
    return get_app(origin)(environ, start_response)

