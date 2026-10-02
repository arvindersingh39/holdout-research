"""r2r: turns a research proposal into publish-ready results with LLM agents and human gates."""

import os

# LiteLLM runs `load_dotenv()` on import in its default DEV mode, copying every `.env` secret into
# os.environ. Secrets reach r2r only through `Settings` and are passed to LiteLLM explicitly.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

__version__ = "0.1.0"
