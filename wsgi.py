"""WSGI entry point for PythonAnywhere deployment (FastAPI → WSGI bridge)"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware
from main import app

application = ASGIMiddleware(app)
