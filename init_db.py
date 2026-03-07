#!/usr/bin/env python
"""Initialize database tables for OpenRabbit."""

from sqlalchemy import create_engine, inspect
from app.config import get_settings
from app.models.database import Base
from app.models.pr_review import PRReview  # noqa: F401
from app.models.tenant import Installation  # noqa: F401

def create_tables():
    """Create all database tables."""
    settings = get_settings()
    engine = create_engine(settings.sync_database_url, echo=False)
    
    try:
        Base.metadata.create_all(engine)
        print("✅ Database tables created successfully!")
        
        # Show created tables
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"\nTables in database: {', '.join(tables)}")
        
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        engine.dispose()
    
    return True

if __name__ == "__main__":
    success = create_tables()
    exit(0 if success else 1)
