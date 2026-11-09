import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix='/api/auth', tags=['auth'])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.get('/session')
def get_session(request: Request) -> dict[str, bool]:
    return {'authenticated': request.session.get('authenticated') is True}


@router.post('/login')
def login(payload: LoginRequest, request: Request) -> dict[str, bool]:
    allowed_passwords = [
        value.strip()
        for value in os.getenv('SITE_ACCESS_PASSWORDS', '').split(',')
        if value.strip()
    ]
    if not allowed_passwords:
        raise HTTPException(
            status_code=503,
            detail='사이트 비밀번호 설정이 완료되지 않았습니다.',
        )
    if not any(
        hmac.compare_digest(payload.password.encode('utf-8'), allowed.encode('utf-8'))
        for allowed in allowed_passwords
    ):
        raise HTTPException(status_code=401, detail='비밀번호가 올바르지 않습니다.')

    request.session.clear()
    request.session['authenticated'] = True
    return {'authenticated': True}


@router.post('/logout')
def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {'authenticated': False}