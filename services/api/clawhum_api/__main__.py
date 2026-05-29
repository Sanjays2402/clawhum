from __future__ import annotations
import uvicorn

if __name__ == "__main__":
    uvicorn.run("clawhum_api.app:app", host="0.0.0.0", port=7451, log_config=None)
