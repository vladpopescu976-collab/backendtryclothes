from app.db.base import Base
from app.db.models import *  # noqa: F401,F403
from app.db.session import SessionLocal, engine
from app.services.seed import seed_reference_data


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_reference_data(db)
        print("Seed completed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

