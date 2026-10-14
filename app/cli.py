import argparse
import getpass
import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.commerce.importer import CommerceImport, import_commerce
from app.commerce.service import seed_commerce
from app.core.auth import bootstrap_user, password_hash
from app.core.config import get_settings
from app.storage.models import User
from app.storage.repository import Repository


def main():
    parser = argparse.ArgumentParser(description="初始化数据库与管理本地账户")
    parser.add_argument("command", choices=["init", "add-user", "import-commerce"])
    parser.add_argument("--email")
    parser.add_argument("--tenant", default="primary")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--sample", action="store_true", help="将导入数据标记为样例")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "init":
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
    repo = Repository(settings)
    if args.command == "init":
        bootstrap_user(repo, settings)
        if settings.seed_demo:
            seed_commerce(repo)
        print("数据库迁移与账户初始化完成；demo 数据：" + str(settings.seed_demo))
    elif args.command == "import-commerce" and args.file:
        payload = CommerceImport.model_validate_json(args.file.read_text(encoding="utf-8"))
        print(
            json.dumps(import_commerce(repo, args.tenant, payload, args.sample), ensure_ascii=False)
        )
    elif args.command == "add-user" and args.email:
        password = getpass.getpass("新账户密码（至少12字符）：")
        if len(password) < 12:
            raise SystemExit("密码过短")
        with repo.session() as db:
            if db.scalar(select(User).where(User.email == args.email.lower())):
                raise SystemExit("账户已存在")
            db.add(
                User(
                    id=str(uuid4()),
                    tenant_id=args.tenant,
                    email=args.email.lower(),
                    password_hash=password_hash(password),
                )
            )
            db.commit()
        print("账户已创建")
    else:
        parser.error("add-user 需要 --email；import-commerce 需要 --file")
    repo.engine.dispose()


if __name__ == "__main__":
    main()
