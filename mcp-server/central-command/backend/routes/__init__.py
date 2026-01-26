"""FastAPI routes for Central Command backend.

This package re-exports the main routes from routes.py (parent level)
to avoid Python module naming conflicts, while also providing the
device_sync sub-module.
"""

import importlib.util
import os

# Load the sibling routes.py file which has the same name as this package
_routes_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "routes.py")
_spec = importlib.util.spec_from_file_location("_parent_routes", _routes_file)
_parent_routes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parent_routes)

# Re-export router and auth_router from the parent routes.py
router = _parent_routes.router
auth_router = _parent_routes.auth_router

# Export device_sync router from sub-module
from .device_sync import router as device_sync_router

__all__ = ["router", "auth_router", "device_sync_router"]
