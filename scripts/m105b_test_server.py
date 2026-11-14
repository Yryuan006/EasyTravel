"""Loopback test composition root; real services/DB, file-controlled risk rule version."""
import asyncio
import os
from pathlib import Path
import sys
import uvicorn
from easytravel.backend.main import create_app
from easytravel.backend.api.v1.trips import confirmation_service
from easytravel.backend.application.confirmations import ConfirmationService
from easytravel.backend.infrastructure.confirmations import unit_of_work
from easytravel.backend.infrastructure.database import load_database_settings

def main():
    assert os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT')=='55434'
    rules=Path(os.environ['M105B_RULES_FILE'])
    app=create_app()
    def service():
        return ConfirmationService(unit_of_work(load_database_settings()),rules_version=rules.read_text(encoding='utf-8'))
    app.dependency_overrides[confirmation_service]=service
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8000,access_log=False,proxy_headers=False))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as runner:
        runner.run(server.serve())

if __name__=='__main__':main()
