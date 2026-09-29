"""SQLite 연결."""
from sqlmodel import Session, SQLModel, create_engine

from app.config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def init_db() -> None:
    from app import models  # noqa: F401  테이블 등록
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
