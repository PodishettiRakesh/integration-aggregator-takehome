from fastapi import Request

from app.integrations.openbao_oauth import OpenBaoOAuthClient


def get_openbao(request: Request) -> OpenBaoOAuthClient:
    return request.app.state.openbao
