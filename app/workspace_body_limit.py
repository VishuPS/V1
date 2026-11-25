"""Bound JSON/file bodies before model parsing, including chunked requests."""
class WorkspaceBodyLimitMiddleware:
    def __init__(self,app,max_bytes=8_000_000):
        self.app=app;self.max_bytes=max_bytes

    async def __call__(self,scope,receive,send):
        path=scope.get('path','')
        if scope['type']!='http' or (not path.startswith('/v1/account/') and path!='/v1/events') or scope.get('method') not in {'POST','PUT','PATCH'}:
            return await self.app(scope,receive,send)
        maximum=4096 if path in {'/v1/events','/v1/account/events'} else self.max_bytes
        messages=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            size+=len(message.get('body',b''))
            if size>maximum:
                body=b'{"detail":"Workspace request body exceeds the upload limit"}'
                await send({'type':'http.response.start','status':413,'headers':[(b'content-type',b'application/json'),(b'cache-control',b'no-store')]})
                return await send({'type':'http.response.body','body':body})
            messages.append(message)
            if not message.get('more_body',False):break
        index=0
        async def replay():
            nonlocal index
            if index<len(messages):
                message=messages[index];index+=1;return message
            return await receive()
        return await self.app(scope,replay,send)
