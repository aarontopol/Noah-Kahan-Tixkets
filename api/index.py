"""Vercel entry point: exposes the hosted UI Flask app as a serverless function.

Vercel's Python runtime serves the WSGI `app` object; vercel.json rewrites all
paths here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hosted.app import create_app  # noqa: E402

app = create_app()
