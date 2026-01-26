"""FastAPI routes for Central Command backend.

This package re-exports the main routes from routes.py (parent level)
to avoid Python module naming conflicts, while also providing the
device_sync sub-module.
"""

import importlib.util
import os
import sys

# Load the sibling routes.py file which has the same name as this package
# We need to set up proper package context for relative imports to work
_dashboard_api_path = os.path.dirname(os.path.dirname(__file__))
_routes_file = os.path.join(_dashboard_api_path, "routes.py")

# Create a spec with proper submodule name so relative imports work
_spec = importlib.util.spec_from_file_location(
    "dashboard_api._routes_impl",  # Use submodule name pattern
    _routes_file,
    submodule_search_locations=[]
)
_parent_routes = importlib.util.module_from_spec(_spec)

# Set package attribute for relative imports
_parent_routes.__package__ = "dashboard_api"

# Add to sys.modules so relative imports can find sibling modules
sys.modules["dashboard_api._routes_impl"] = _parent_routes

# Execute the module
_spec.loader.exec_module(_parent_routes)

# Re-export router and auth_router from the parent routes.py
router = _parent_routes.router
auth_router = _parent_routes.auth_router

# Export device_sync router from sub-module
from .device_sync import router as device_sync_router

__all__ = ["router", "auth_router", "device_sync_router"]
