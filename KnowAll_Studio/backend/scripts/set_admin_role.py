"""Set an existing user's administrator role from the backend host.

Run from the backend directory after migrations:
    python -m scripts.set_admin_role grant --user-id <user UUID>
    python -m scripts.set_admin_role revoke --user-id <user UUID>

This command is intentionally interactive and never selects an account by name.
"""

import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.database import async_session
from app.models.user import User


def validate_role_change(user: User | None, action: str, active_admin_count: int) -> None:
    """Reject changes that would grant inactive users or remove the last admin."""
    if user is None:
        raise ValueError("找不到指定的用户 ID")
    if action == "grant":
        if not user.is_active:
            raise ValueError("不能将已禁用的账户设为管理员")
        if user.is_admin:
            raise ValueError("该用户已经是管理员")
    elif action == "revoke":
        if not user.is_admin:
            raise ValueError("该用户不是管理员")
        if user.is_active and active_admin_count <= 1:
            raise ValueError("不能撤销最后一位管理员")
    else:
        raise ValueError("未知操作")


async def _load_target_and_count(db, user_id: str):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    active_admin_count = (await db.execute(
        select(func.count()).select_from(User).where(
            User.is_admin.is_(True), User.is_active.is_(True)
        )
    )).scalar_one()
    return user, active_admin_count


async def set_admin_role(action: str, user_id: str) -> None:
    async with async_session() as db:
        user, active_admin_count = await _load_target_and_count(db, user_id)
        validate_role_change(user, action, active_admin_count)
        print(f"目标账户: {user.id} / {user.username} / {user.email}")
        print(f"当前可用管理员数量: {active_admin_count}; 操作: {action}")
        if not sys.stdin.isatty():
            raise ValueError("需要在本机交互终端确认")
        if input("请再次输入目标用户 ID 以确认: ").strip() != user_id:
            raise ValueError("确认的用户 ID 不匹配")

        # Re-read after operator input so an intervening role change is checked.
        await db.rollback()
        user, active_admin_count = await _load_target_and_count(db, user_id)
        validate_role_change(user, action, active_admin_count)
        user.is_admin = action == "grant"
        await db.commit()
        print("管理员权限已更新")


def main() -> None:
    parser = argparse.ArgumentParser(description="本机设置现有用户的管理员权限")
    parser.add_argument("action", choices=("grant", "revoke"))
    parser.add_argument("--user-id", required=True, help="目标用户的完整 ID")
    args = parser.parse_args()
    try:
        asyncio.run(set_admin_role(args.action, args.user_id.strip()))
    except (ValueError, KeyboardInterrupt) as exc:
        parser.exit(1, f"操作未执行: {exc}\n")


if __name__ == "__main__":
    main()
