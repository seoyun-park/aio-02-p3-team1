from fastapi import HTTPException, Request


def require_authenticated_session(request: Request) -> None:
    if request.session.get('authenticated') is not True:
        raise HTTPException(status_code=401, detail='로그인이 필요합니다.')