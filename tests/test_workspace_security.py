import asyncio
from app.workspace_body_limit import WorkspaceBodyLimitMiddleware
from tests.test_platform_phase2 import account


def test_chunked_workspace_body_is_bounded_before_downstream():
    calls=[];sent=[]
    async def downstream(scope,receive,send):calls.append(True)
    messages=iter([{'type':'http.request','body':b'12345','more_body':True},{'type':'http.request','body':b'67890','more_body':False}])
    async def receive():return next(messages)
    async def send(message):sent.append(message)
    middleware=WorkspaceBodyLimitMiddleware(downstream,max_bytes=8)
    asyncio.run(middleware({'type':'http','path':'/v1/account/bulk/export','method':'POST'},receive,send))
    assert not calls and sent[0]['status']==413


def test_workspace_put_preflight_and_untrusted_origin(unauthenticated_client,session_factory):
    c=unauthenticated_client
    response=c.options('/v1/account/vault/test',headers={'Origin':'http://localhost:3000','Access-Control-Request-Method':'PUT','Access-Control-Request-Headers':'content-type'})
    assert response.status_code==200 and response.headers['access-control-allow-origin']=='http://localhost:3000'
    _,h=account(c,session_factory)
    assert c.post('/v1/account/skus/generate',headers={**h,'Origin':'https://untrusted.example'},json={'quantity':1}).status_code==403
