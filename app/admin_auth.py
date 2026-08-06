from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.user_auth import UserContext, get_current_user


def require_admin(
    context: Annotated[UserContext, Depends(get_current_user)],
) -> UserContext:
    if not context.user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "admin_required",
                "message": "Administrator access is required",
            },
        )
    return context


AdminContext = Annotated[UserContext, Depends(require_admin)]
